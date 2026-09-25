"use client";
// /acquisition/intake/[id] 一批收件的估價與叫號確認（docs/42 §4、§5）。
// 估價隨時存檔、可中途離開再回來；估完送去叫號；叫號時逐列標處置（可部分接受）。
// 客人同意後送顧客螢幕簽一次、付款；付款就成立收購、商品進「待整理」（I3）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useEffect, useRef, useState } from "react";

import { GRADE_LABEL } from "@/features/acquisition/labels";
import { LineForm, type LineFields } from "@/features/intake/LineForm";
import { pctToDiscount, pricingRates } from "@/features/intake/estimate";
import { DISPOSITION_LABEL } from "@/features/intake/labels";
import { PaymentPanel, useSignatureLock } from "@/features/intake/PaymentPanel";
import { IntakeSteps, NEXT_STEP, StatusBadge } from "@/features/intake/StatusBadge";
import { printSlip } from "@/features/intake/print";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type Batch = components["schemas"]["IntakeBatchRead"];
type Line = components["schemas"]["IntakeLineRead"];
type Disposition = components["schemas"]["IntakeDisposition"];

const TYPE_LABEL = { BUYOUT: "買斷", CONSIGNMENT: "寄售", BULK_LOT: "散裝" } as const;
const EDITABLE = new Set(["PENDING_ESTIMATE", "ESTIMATING", "AWAITING_CONFIRM"]);
const DELETABLE = new Set(["PENDING_ESTIMATE", "ESTIMATING"]);
const PAID_STATUSES = new Set(["PAID", "PARTIALLY_LISTED", "LISTED"]);

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

function money(value: string | null | undefined): string {
  if (value == null) return "—";
  const n = parseNtd(value);
  return n === null ? "—" : `$${formatNtd(n)}`;
}

function DispositionControls({
  batch,
  line,
  onSaved,
}: {
  batch: Batch;
  line: Line;
  onSaved: () => void;
}) {
  const [disposition, setDisposition] = useState<Disposition>(line.disposition);
  const [accepted, setAccepted] = useState(String(line.accepted_qty || line.qty));
  const [returned, setReturned] = useState(line.returned_to_customer);
  const [error, setError] = useState<string | null>(null);
  const cancelled = batch.status === "CANCELLED";

  const save = useMutation({
    mutationFn: async () => {
      const acceptedQty = disposition === "ACCEPTED" ? parseNtd(accepted) : 0;
      if (acceptedQty === null) throw new Error("接受件數請填整數");
      const { data, error: apiErr } = await api.PATCH(
        "/api/v1/intake-batches/{batch_id}/lines/{line_id}/disposition",
        {
          params: { path: { batch_id: batch.id, line_id: line.id } },
          body: { disposition, accepted_qty: acceptedQty, returned_to_customer: returned },
        },
      );
      if (!data) throw new Error(detail(apiErr) ?? "儲存失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onSaved();
    },
    onError: (e: Error) => setError(e.message),
  });

  const acceptedQty = disposition === "ACCEPTED" ? (parseNtd(accepted) ?? 0) : 0;
  const leftover = line.qty - acceptedQty;
  const options: Disposition[] = cancelled
    ? ["PENDING", "CUSTOMER_KEPT", "STORE_DECLINED"]
    : ["PENDING", "ACCEPTED", "CUSTOMER_KEPT", "STORE_DECLINED"];

  return (
    <div className="intake-disposition">
      <select
        aria-label={`第 ${line.line_no} 列處置`}
        value={disposition}
        onChange={(e) => setDisposition(e.target.value as Disposition)}
      >
        {options.map((d) => (
          <option key={d} value={d}>
            {DISPOSITION_LABEL[d]}
          </option>
        ))}
      </select>
      {disposition === "ACCEPTED" && line.qty > 1 && (
        <label className="intake-inline-field">
          收
          <select
            aria-label={`第 ${line.line_no} 列接受件數`}
            value={accepted}
            onChange={(e) => setAccepted(e.target.value)}
          >
            {Array.from({ length: line.qty }, (_, i) => String(i + 1)).map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          ／{line.qty} 件
        </label>
      )}
      {disposition === "ACCEPTED" && line.acquisition_type !== "CONSIGNMENT" && line.deal_cost != null && (
        <span className="intake-line-pay">
          付 {money(line.deal_cost)} × {acceptedQty} 件＝
          <strong>${formatNtd((parseNtd(line.deal_cost) ?? 0) * acceptedQty)}</strong>
        </span>
      )}
      {leftover > 0 && disposition !== "PENDING" && (
        <label className="campaign-checkbox">
          <input type="checkbox" checked={returned} onChange={(e) => setReturned(e.target.checked)} />
          沒收的 {leftover} 件已交還客人
        </label>
      )}
      <button type="button" className="btn-secondary" disabled={save.isPending} onClick={() => save.mutate()}>
        儲存
      </button>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </div>
  );
}

function IntakeBatchContent() {
  const params = useParams<{ id: string }>();
  const batchId = Number(params.id);
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<number | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [askCancel, setAskCancel] = useState(false);
  const searchParams = useSearchParams();
  const router = useRouter();
  const [printNotice, setPrintNotice] = useState<string | null>(null);
  const autoPrinted = useRef(false);

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.GET("/api/v1/settings")).data ?? null,
  });
  const rates = pricingRates(settings.data);
  const drawer = useQuery({
    queryKey: ["cash-session", "current"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/cash-sessions/current");
      return response.status === 200 ? (data ?? null) : null;
    },
  });
  const defaultCommission = settings.data?.default_commission_pct ?? null;

  const batchQuery = useQuery({
    queryKey: ["intake-batch", batchId],
    queryFn: async () => {
      const { data, error: apiErr } = await api.GET("/api/v1/intake-batches/{batch_id}", {
        params: { path: { batch_id: batchId } },
      });
      if (!data) throw new Error(detail(apiErr) ?? "讀取失敗");
      return data;
    },
    enabled: Number.isFinite(batchId),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["intake-batch", batchId] });
    void queryClient.invalidateQueries({ queryKey: ["intake-batches"] });
  };

  const addLine = useMutation({
    mutationFn: async (fields: LineFields) => {
      const { data, error: apiErr } = await api.POST("/api/v1/intake-batches/{batch_id}/lines", {
        params: { path: { batch_id: batchId } },
        body: {
          ...fields,
          short_name: fields.short_name ?? "",
          qty: fields.qty ?? 1,
          acquisition_type: fields.acquisition_type ?? "BUYOUT",
        },
      });
      if (!data) throw new Error(detail(apiErr) ?? "儲存失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      setFormKey((k) => k + 1); // 清空表單、準備下一件
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const updateLine = useMutation({
    mutationFn: async ({ lineId, fields }: { lineId: number; fields: LineFields }) => {
      const { data, error: apiErr } = await api.PATCH(
        "/api/v1/intake-batches/{batch_id}/lines/{line_id}",
        { params: { path: { batch_id: batchId, line_id: lineId } }, body: fields },
      );
      if (!data) throw new Error(detail(apiErr) ?? "儲存失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      setEditing(null);
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const deleteLine = useMutation({
    mutationFn: async (lineId: number) => {
      const { error: apiErr, response } = await api.DELETE(
        "/api/v1/intake-batches/{batch_id}/lines/{line_id}",
        { params: { path: { batch_id: batchId, line_id: lineId } } },
      );
      if (!response.ok) throw new Error(detail(apiErr) ?? "刪除失敗");
    },
    onSuccess: refresh,
    onError: (e: Error) => setError(e.message),
  });

  const markReady = useMutation({
    mutationFn: async () => {
      const { data, error: apiErr } = await api.POST("/api/v1/intake-batches/{batch_id}/ready", {
        params: { path: { batch_id: batchId } },
      });
      if (!data) throw new Error(detail(apiErr) ?? "送出失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const reprint = useMutation({
    mutationFn: async ({ batch, copies }: { batch: Batch; copies: number }) => {
      await printSlip(batch, copies);
    },
    onSuccess: (_data, { copies }) =>
      setPrintNotice(copies === 2 ? "收件單已送出列印（兩份）。" : "收件單已送出列印。"),
    onError: (e: Error) =>
      setPrintNotice(`收件單沒有印出來：${e.message}。號碼已登記，可按「補印收件單」。`),
  });

  // 剛報到（?print=new）：自動印兩份，只印一次；印完把參數拿掉，重新整理才不會再印。
  const batchForPrint = batchQuery.data;
  useEffect(() => {
    if (searchParams.get("print") !== "new" || !batchForPrint || autoPrinted.current) return;
    autoPrinted.current = true;
    reprint.mutate({ batch: batchForPrint, copies: 2 });
    router.replace(`/acquisition/intake/${batchForPrint.id}`);
  }, [searchParams, batchForPrint, reprint, router]);

  const cancel = useMutation({
    mutationFn: async () => {
      if (!cancelReason.trim()) throw new Error("請填取消原因");
      const { data, error: apiErr } = await api.POST("/api/v1/intake-batches/{batch_id}/cancel", {
        params: { path: { batch_id: batchId } },
        body: { reason: cancelReason.trim() },
      });
      if (!data) throw new Error(detail(apiErr) ?? "取消失敗");
      return data;
    },
    onSuccess: () => {
      setAskCancel(false);
      setError(null);
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const signature = useSignatureLock(batchQuery.data);

  if (batchQuery.isError) {
    return (
      <section className="intake-page">
        <p role="alert" className="form-error">找不到這一批，或讀取失敗。</p>
        <Link href="/acquisition/intake">回排隊清單</Link>
      </section>
    );
  }
  const batch = batchQuery.data;
  if (!batch) return <p className="hint">讀取中…</p>;

  const editable = EDITABLE.has(batch.status);
  // 估完（待確認）時從清單按「編輯」進來＝編輯模式：回到新增／編輯商品，暫時收起叫號處置。
  const editMode = batch.status === "AWAITING_CONFIRM" && searchParams.get("mode") === "edit";
  const confirming =
    (batch.status === "AWAITING_CONFIRM" && !editMode) || batch.status === "CANCELLED";
  // 估價中還沒估完是正常的：只提示還差幾件；估完（或估多了）才用紅字請店員再點一次。
  const missingItems = batch.declared_item_count - batch.item_count;
  const stillEstimating = batch.status === "PENDING_ESTIMATE" || batch.status === "ESTIMATING";
  const consignmentAccepted = batch.lines
    .filter((l) => l.acquisition_type === "CONSIGNMENT" && l.disposition === "ACCEPTED")
    .reduce((sum, l) => sum + l.accepted_qty, 0);
  const countMismatch =
    batch.line_count > 0 && missingItems !== 0 && (!stillEstimating || missingItems < 0);

  return (
    <section className="intake-page">
      <div className="pur-page-head">
        <h1 className="page-title">
          <span className="intake-ticket-big">{batch.ticket_label}</span> {batch.contact_name}
        </h1>
        <Link href="/acquisition/intake" className="btn-ghost">
          回排隊清單
        </Link>
      </div>
      <IntakeSteps status={batch.status} />
      <p className="intake-next" role="status">
        下一步：
        {editMode
          ? "改好商品後按「回到叫號確認」。估完的商品不能刪除，客人不要的請在叫號時選「客人不售／店家不收」。"
          : NEXT_STEP[batch.status]}
      </p>
      <div className="card intake-summary">
        <span>
          狀態：<StatusBadge status={batch.status} />
        </span>
        <span>報到 {formatTaipeiDateTime(batch.created_at, { omitYear: true })}</span>
        <span>
          實收 {batch.declared_item_count} 件・已估 {batch.item_count} 件
        </span>
        {!confirming && (
          <span>
            估價收購總額 <strong className="money">{money(batch.deal_total)}</strong>
          </span>
        )}
        {batch.note && <span>備註：{batch.note}</span>}
        {batch.cancel_reason && <span>取消原因：{batch.cancel_reason}</span>}
      </div>
      {stillEstimating && missingItems > 0 && (
        <p className="intake-callout-warn" role="status">
          還有 <strong>{missingItems} 件</strong>沒估（報到時點清 {batch.declared_item_count} 件、已估{" "}
          {batch.item_count} 件）。
        </p>
      )}
      {batch.status === "AWAITING_CONFIRM" && !editMode && (
        <div className="card intake-payout" aria-label="要付給客人">
          <span className="intake-payout-label">要付給客人</span>
          <strong className="intake-payout-amount">{money(batch.accepted_total)}</strong>
          <span>
            收 {batch.accepted_item_count} 件（共 {batch.item_count} 件）
            {consignmentAccepted > 0 ? `・其中寄售 ${consignmentAccepted} 件，賣出後才分帳、現在不付錢` : ""}
          </span>
          <span className="hint">
            只算已選「接受」的商品；還沒談定的不算進去。原本估價 {money(batch.deal_total)}。
          </span>
        </div>
      )}
      {countMismatch && (
        <p className="form-error" role="status">
          已估 {batch.item_count} 件，與報到時點清的 {batch.declared_item_count} 件不同，請再點一次。
        </p>
      )}
      <div className="intake-print">
        <button
          type="button"
          className="btn-secondary"
          disabled={reprint.isPending}
          onClick={() => reprint.mutate({ batch, copies: 2 })}
        >
          補印收件單（兩份）
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={reprint.isPending}
          onClick={() => reprint.mutate({ batch, copies: 1 })}
        >
          補印一份
        </button>
        {printNotice !== null && (
          <span role="status" className="hint">
            {printNotice}
          </span>
        )}
      </div>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}

      <div className="card">
        <h2>估價明細</h2>
        {batch.lines.length === 0 ? (
          <p className="hint">還沒有估價：請在下方「新增一件商品」填好後按「＋ 加入這一件」。</p>
        ) : (
          <div className="intake-table-scroll">
          <table className="intake-lines">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">簡稱</th>
                <th scope="col">類型</th>
                <th scope="col">數量</th>
                <th scope="col">原價</th>
                <th scope="col">折數</th>
                <th scope="col">預計售價</th>
                <th scope="col">成交收購價</th>
                <th scope="col">成色</th>
                <th scope="col" aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              {batch.lines.map((line) =>
                editing === line.id ? (
                  <tr key={line.id}>
                    <td colSpan={10}>
                      <LineForm
                        initial={line}
                        rates={rates}
                        defaultCommissionPct={defaultCommission}
                        submitLabel="儲存修改"
                        busy={updateLine.isPending}
                        onSubmit={(fields) => updateLine.mutate({ lineId: line.id, fields })}
                        onCancel={() => setEditing(null)}
                      />
                    </td>
                  </tr>
                ) : (
                  <Fragment key={line.id}>
                  <tr className={confirming ? "intake-has-disposition" : undefined}>
                    <td>{line.line_no}</td>
                    <td className="intake-wrap">
                      {line.short_name}
                      {line.note && <span className="row-sub">{line.note}</span>}
                    </td>
                    <td>{TYPE_LABEL[line.acquisition_type]}</td>
                    <td>{line.qty}</td>
                    <td>{money(line.reference_price)}</td>
                    <td>{line.discount_pct != null ? `${pctToDiscount(line.discount_pct)} 折` : "—"}</td>
                    <td>{money(line.expected_listed_price)}</td>
                    <td>
                      {line.acquisition_type === "CONSIGNMENT"
                        ? `抽成 ${line.commission_pct ?? "—"}%`
                        : money(line.deal_cost)}
                    </td>
                    <td>{line.grade ? GRADE_LABEL[line.grade] : "—"}</td>
                    <td>
                      {editable && (
                        <div className="intake-row-actions">
                          <button type="button" className="btn-ghost" onClick={() => setEditing(line.id)}>
                            編輯
                          </button>
                          {DELETABLE.has(batch.status) && (
                            <button
                              type="button"
                              className="btn-ghost"
                              aria-label={`刪除第 ${line.line_no} 列`}
                              onClick={() => deleteLine.mutate(line.id)}
                            >
                              刪除
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                  {/* 叫號處置自成一列：放在最後一欄會把按鈕擠成直排。 */}
                  {confirming && (
                    <tr className="intake-disposition-row">
                      <td />
                      <td colSpan={9}>
                        {/* 送簽後鎖住：改了就和客人簽的不一樣（要改先撤回簽名）。 */}
                        <fieldset className="intake-disposition-lock" disabled={signature.locked}>
                          <DispositionControls batch={batch} line={line} onSaved={refresh} />
                        </fieldset>
                      </td>
                    </tr>
                  )}
                  </Fragment>
                ),
              )}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {editable && (batch.status !== "AWAITING_CONFIRM" || editMode) && (
        <div className="card">
          <h2>新增一件商品</h2>
          <p className="hint">
            填簡稱、原價、點折數（收購價會自動帶出，可改），按「＋ 加入這一件」就會出現在上面的估價明細；
            {editMode
              ? "改好後按最下面的「回到叫號確認」。"
              : "可以一直加，全部估完再按最下面的「估完，送去叫號」。"}
          </p>
          <LineForm
            key={formKey}
            rates={rates}
            defaultCommissionPct={defaultCommission}
            submitLabel="＋ 加入這一件"
            busy={addLine.isPending}
            onSubmit={(fields) => addLine.mutate(fields)}
          />
        </div>
      )}

      {((batch.status === "AWAITING_CONFIRM" && !editMode) || PAID_STATUSES.has(batch.status)) && (
        <PaymentPanel
          batch={batch}
          signature={signature}
          requireSignature={settings.data?.require_acquisition_affidavit ?? false}
          drawerOpen={drawer.data != null}
          onChanged={() => {
            refresh();
            void queryClient.invalidateQueries({ queryKey: ["signing-task"] });
            void queryClient.invalidateQueries({ queryKey: ["cash-session"] });
          }}
        />
      )}

      <div className="intake-footer">
        {batch.status === "ESTIMATING" && (
          <button type="button" className="btn-primary" disabled={markReady.isPending} onClick={() => markReady.mutate()}>
            估完，送去叫號
          </button>
        )}
        {editMode && (
          <Link href={`/acquisition/intake/${batch.id}`} className="btn-primary">
            回到叫號確認
          </Link>
        )}
        {editable && !askCancel && !signature.locked && (
          <button type="button" className="btn-ghost" onClick={() => setAskCancel(true)}>
            取消整批
          </button>
        )}
        {askCancel && (
          <div className="intake-cancel">
            <label className="field">
              <span className="field-label">取消原因</span>
              <input aria-label="取消原因" value={cancelReason} maxLength={200}
                onChange={(e) => setCancelReason(e.target.value)} />
            </label>
            <button type="button" className="btn-primary" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
              確定取消
            </button>
            <button type="button" className="btn-ghost" onClick={() => setAskCancel(false)}>
              不取消
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

export default function IntakeBatchPage() {
  // useSearchParams 需要 Suspense 邊界（Next App Router）。
  return (
    <Suspense fallback={<p className="hint">讀取中…</p>}>
      <IntakeBatchContent />
    </Suspense>
  );
}
