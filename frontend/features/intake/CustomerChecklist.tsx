"use client";
// 給客人勾選要賣哪幾件（docs/42 §13；店主 2026-10-02）：店員把平板遞給客人，全螢幕大字、
// 只顯示號碼、名稱與收購價——不顯示預計售價、成本、毛利等店內資訊。預設照目前勾選（估完時全勾），
// 沒勾＝客人不賣、交還客人。按「確認」直接進同一台平板的簽署頁（切結書、現金或購物金、簽名），
// 簽署頁可以回上一頁重勾；簽完請客人把平板交還店員付款。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { displayName } from "@/features/intake/QuickEstimate";
import { TabletSigning } from "@/features/intake/TabletSigning";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Batch = components["schemas"]["IntakeBatchRead"];
type Line = components["schemas"]["IntakeLineRead"];
type Task = components["schemas"]["KioskTaskRead"];

const PAYOUT_LABEL = { CASH: "現金", STORE_CREDIT: "購物金" } as const;

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

function amount(line: Line): number {
  return line.acquisition_type === "CONSIGNMENT" ? 0 : (parseNtd(line.deal_cost ?? "") ?? 0) * line.qty;
}

export function CustomerChecklist({
  batch,
  onDone,
  onClose,
}: {
  batch: Batch;
  onDone: () => void;
  onClose: () => void;
}) {
  const [checked, setChecked] = useState<Set<number>>(
    () => new Set(batch.lines.filter((l) => l.disposition !== "CUSTOMER_KEPT").map((l) => l.id)),
  );
  // 進了簽署頁就是那份任務；回上一頁清掉，再確認會建新任務（內容照新的勾選）。
  const [task, setTask] = useState<Task | null>(null);
  // 簽完選的收款方式；只賣寄售沒得選＝NONE。
  const [signedPayout, setSignedPayout] = useState<keyof typeof PAYOUT_LABEL | "NONE" | null>(null);
  // 客人全部取消勾選、按了「確認都不賣」（店主 2026-10-03）。
  const [declined, setDeclined] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selling = batch.lines.filter((l) => checked.has(l.id));
  const count = selling.reduce((n, l) => n + l.qty, 0);
  const total = selling.reduce((n, l) => n + amount(l), 0);

  const confirm = useMutation({
    mutationFn: async () => {
      const kept = batch.lines.filter((l) => !checked.has(l.id)).map((l) => l.id);
      const { data, error: apiErr } = await api.POST("/api/v1/intake-batches/{batch_id}/customer-confirm", {
        params: { path: { batch_id: batch.id } },
        body: { kept_line_ids: kept },
      });
      if (!data) throw new Error(detail(apiErr) ?? "確認失敗，請交給店員");
      const started = await api.POST("/api/v1/intake-batches/{batch_id}/tablet-signature", {
        params: { path: { batch_id: batch.id } },
      });
      if (!started.data) throw new Error(detail(started.error) ?? "沒辦法開始簽署，請交給店員");
      return started.data;
    },
    onSuccess: (started) => {
      setError(null);
      setTask(started);
      onDone();
    },
    onError: (e: Error) => setError(e.message),
  });

  const decline = useMutation({
    mutationFn: async () => {
      const { data, error: apiErr } = await api.POST("/api/v1/intake-batches/{batch_id}/customer-decline", {
        params: { path: { batch_id: batch.id } },
      });
      if (!data) throw new Error(detail(apiErr) ?? "送出失敗，請交給店員");
      return data;
    },
    onSuccess: () => {
      setError(null);
      setDeclined(true);
      onDone();
    },
    onError: (e: Error) => setError(e.message),
  });

  function toggle(id: number) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (task !== null && signedPayout === null) {
    return (
      <TabletSigning
        key={task.id}
        task={task}
        onBack={() => setTask(null)}
        onSigned={(payout) => {
          setSignedPayout(payout ?? "NONE");
          onDone();
        }}
      />
    );
  }

  return (
    <div className="intake-customer" role="dialog" aria-modal="true" aria-labelledby="intake-customer-title">
      <div className="intake-customer-inner">
        {!declined && signedPayout === null && (
          <button
            type="button"
            className="btn-ghost intake-customer-back"
            disabled={confirm.isPending || decline.isPending}
            onClick={onClose}
          >
            返回店員頁面
          </button>
        )}
        <h2 id="intake-customer-title">{signedPayout ? "簽署完成" : "請確認要賣的商品"}</h2>
        {declined ? (
          <div className="intake-customer-done">
            <p className="intake-customer-big">好的，這次都不賣。</p>
            <p>請把平板交還給店員，並取回您的商品。</p>
            <button type="button" className="btn-primary intake-customer-btn" onClick={onClose}>
              交還店員
            </button>
          </div>
        ) : signedPayout ? (
          <div className="intake-customer-done">
            <p className="intake-customer-big">謝謝！請把平板交還給店員。</p>
            <p>
              共 {count} 件，收購價合計 <strong>${formatNtd(total)}</strong>
              {signedPayout === "NONE" ? "（寄售，賣出後分帳）" : `，選擇拿${PAYOUT_LABEL[signedPayout]}`}
            </p>
            <button type="button" className="btn-primary intake-customer-btn" onClick={onClose}>
              交還店員
            </button>
          </div>
        ) : (
          <>
            <p className="intake-customer-hint">
              號碼牌 {batch.ticket_label}・{batch.contact_name}　有打勾的是要賣的；不賣的請取消勾選。
            </p>
            <ul className="intake-customer-list">
              {batch.lines.map((line) => {
                const name = displayName(line);
                const price =
                  line.acquisition_type === "CONSIGNMENT"
                    ? `寄售 $${formatNtd(parseNtd(line.expected_listed_price ?? "") ?? 0)}`
                    : `$${formatNtd(amount(line))}`;
                return (
                  <li key={line.id}>
                    <label className={`intake-customer-row${checked.has(line.id) ? " is-on" : ""}`}>
                      <input
                        type="checkbox"
                        checked={checked.has(line.id)}
                        onChange={() => toggle(line.id)}
                      />
                      <span className="intake-customer-no">{line.line_no} 號</span>
                      <span className="intake-customer-name">
                        {name ?? ""}
                        {line.qty > 1 ? ` ×${line.qty}` : ""}
                        {line.bulk_piece_count != null ? ` ×${line.bulk_piece_count}` : ""}
                      </span>
                      <span className="intake-customer-price">{price}</span>
                    </label>
                  </li>
                );
              })}
            </ul>
            <p className="intake-customer-total" role="status">
              共 {count} 件，收購價合計 <strong>${formatNtd(total)}</strong>
            </p>
            {count === 0 && <p className="intake-customer-hint">都沒有打勾＝這次都不賣，商品全部帶回。</p>}
            {error !== null && (
              <p role="alert" className="form-error">
                {error}
              </p>
            )}
            <div className="intake-customer-actions intake-customer-actions-end">
              {count === 0 ? (
                <button
                  type="button"
                  className="btn-danger intake-customer-btn"
                  disabled={decline.isPending}
                  onClick={() => decline.mutate()}
                >
                  {decline.isPending ? "送出中…" : "確認都不賣"}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn-primary intake-customer-btn"
                  disabled={confirm.isPending}
                  onClick={() => confirm.mutate()}
                >
                  {confirm.isPending ? "送出中…" : "確認"}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
