"use client";
// 採購單的日期與付款（docs/99 §5、行事曆 1B）：實際下單日、預計到貨日、付款條件、付款期限。
//
// 誰能改（docs/99 §7）：預計到貨日店員也能改（廠商說會晚到，接到電話的人就改）；
// 實際下單日送出後只有管理者能改；付款期限只有管理者看得到、能改，也能恢復自動計算。
// 改了會出現在行事曆，所以存檔後連行事曆的資料一起重抓。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { formatSlashDate } from "@/features/agenda/calendar";
import { isLateDelivery, paymentTermsLabel } from "@/features/purchasing/purchasing";
import { extractDetail, type PurchaseOrder } from "@/features/purchasing/shared";
import { api } from "@/lib/api";
import { taipeiDate } from "@/lib/datetime";
import { useCurrentRole } from "@/lib/useCurrentRole";

type Field = "ordered_on" | "expected_on" | "payment_due_on";
type ScheduleBody = Partial<Record<Field, string | null>>;

const FIELD_LABEL: Record<Field, string> = {
  ordered_on: "實際下單日",
  expected_on: "預計到貨日",
  payment_due_on: "付款期限",
};

export function PurchaseOrderSchedule({ po }: { po: PurchaseOrder }) {
  const queryClient = useQueryClient();
  const { isManager } = useCurrentRole();
  const [editing, setEditing] = useState<Field | null>(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const today = taipeiDate();
  const draft = po.status === "DRAFT";
  const cancelled = po.status === "CANCELLED";

  const save = useMutation({
    mutationFn: async (body: ScheduleBody) => {
      const { data, error: apiError } = await api.PATCH(
        "/api/v1/purchase-orders/{purchase_order_id}/schedule",
        { params: { path: { purchase_order_id: po.id } }, body },
      );
      if (!data) throw new Error(extractDetail(apiError) ?? "儲存日期失敗");
      return data;
    },
    onSuccess: async () => {
      setEditing(null);
      setError(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["purchase-orders"] }),
        queryClient.invalidateQueries({ queryKey: ["agenda"] }),
      ]);
    },
    onError: (err: Error) => setError(err.message),
  });

  const startEdit = (field: Field, current: string | null | undefined) => {
    setError(null);
    setEditing(field);
    setValue(current ?? (field === "ordered_on" ? today : ""));
  };

  const editor = (field: Field) => (
    <span className="pur-schedule-edit">
      <input
        type="date"
        aria-label={FIELD_LABEL[field]}
        value={value}
        max={field === "ordered_on" ? today : undefined}
        onChange={(e) => setValue(e.target.value)}
      />
      <button
        type="button"
        className="btn-secondary"
        disabled={save.isPending || (field === "ordered_on" && value === "")}
        onClick={() => save.mutate({ [field]: value === "" ? null : value })}
      >
        {save.isPending ? "儲存中…" : "儲存"}
      </button>
      <button type="button" className="btn-ghost" disabled={save.isPending} onClick={() => setEditing(null)}>
        取消
      </button>
    </span>
  );

  const late = isLateDelivery(po, today);
  const canEditExpected = !cancelled && po.status !== "RECEIVED";
  const canEditOrdered = isManager && !draft && !cancelled;
  const canEditDue = isManager && !draft && !cancelled;

  return (
    <div className="pur-schedule">
      <h3>日期與付款</h3>
      <dl className="pur-detail-grid">
        <div>
          <dt>實際下單日</dt>
          <dd>
            {editing === "ordered_on" ? (
              editor("ordered_on")
            ) : (
              <>
                {draft ? "送出時決定" : po.ordered_on ? formatSlashDate(po.ordered_on) : "—"}
                {canEditOrdered && (
                  <button type="button" className="btn-ghost pur-schedule-btn" onClick={() => startEdit("ordered_on", po.ordered_on)}>
                    改
                  </button>
                )}
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>預計到貨日</dt>
          <dd>
            {editing === "expected_on" ? (
              editor("expected_on")
            ) : (
              <>
                {po.expected_on ? formatSlashDate(po.expected_on) : "未填"}
                {late && <span className="pur-late">（逾期未到）</span>}
                {canEditExpected && (
                  <button type="button" className="btn-ghost pur-schedule-btn" onClick={() => startEdit("expected_on", po.expected_on)}>
                    {po.expected_on ? "改" : "填"}
                  </button>
                )}
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>付款條件</dt>
          <dd>{draft ? "送出時帶入供應商的條件" : paymentTermsLabel(po.payment_terms, po.payment_terms_value)}</dd>
        </div>
        {isManager && (
          <div>
            <dt>付款期限</dt>
            <dd>
              {editing === "payment_due_on" ? (
                editor("payment_due_on")
              ) : (
                <>
                  {po.payment_due_on
                    ? `${formatSlashDate(po.payment_due_on)}${po.payment_due_overridden ? "（手動）" : ""}`
                    : draft
                      ? "—"
                      : "還算不出來（例如月結要等到貨）"}
                  {canEditDue && (
                    <button type="button" className="btn-ghost pur-schedule-btn" onClick={() => startEdit("payment_due_on", po.payment_due_on)}>
                      改
                    </button>
                  )}
                  {canEditDue && po.payment_due_overridden && (
                    <button
                      type="button"
                      className="btn-ghost pur-schedule-btn"
                      disabled={save.isPending}
                      onClick={() => save.mutate({ payment_due_on: null })}
                    >
                      恢復自動
                    </button>
                  )}
                </>
              )}
            </dd>
          </div>
        )}
      </dl>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </div>
  );
}
