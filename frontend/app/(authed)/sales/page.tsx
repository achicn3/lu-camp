"use client";
// /sales 交易紀錄（當日）：打錯單的現場救援入口——列出今日銷售、店長可作廢（二次確認，
// docs/10 §28 危險動作）。作廢由後端整套反轉：庫存回補、點數/購物金沖回、寄售結算反轉、
// 電子發票中止；已退貨/已作廢的單後端會擋（409），前端先行停用按鈕。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { INVOICE_STATUS_LABELS, labelFor } from "@/features/member/labels";
import { SignatureEvidenceDialog } from "@/features/signing/SignatureEvidenceDialog";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import {
  formatTaipeiDateTime,
  formatTaipeiTime,
  startOfTaipeiDay,
  taipeiDate,
} from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";
import {
  clearPersistedIdemKey,
  getOrCreatePersistedIdemKey,
} from "@/lib/idempotency";
import {
  computeRefund,
  isReturnable,
  remainingQty,
  validateReturnPlan,
} from "@/features/returns/plan";
import {
  refundPlan,
  refundTenderLabel,
  supportsRefund,
} from "@/features/returns/refund";

type SaleSummary = components["schemas"]["SaleSummaryRead"];
type ReturnTenderRead = components["schemas"]["ReturnTenderRead"];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function useIsManager(): boolean {
  return useMemo(() => decodeSession()?.role === "MANAGER", []);
}

/** 今日台灣 00:00 → UTC ISO；「當日交易」固定依門市營業日。 */
function startOfTodayIso(): string {
  return startOfTaipeiDay(taipeiDate());
}

function timeLabel(iso: string): string {
  return formatTaipeiTime(iso);
}

const SALE_STATUS_LABELS: Record<string, string> = {
  COMPLETED: "已完成",
  RETURNED: "已退貨",
};

function VoidConfirmDialog({
  sale,
  onClose,
  onVoided,
}: {
  sale: SaleSummary;
  onClose: () => void;
  onVoided: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  // 台灣Pay 無 API 退款（docs/30 finding #3）：作廢須店員先於台灣Pay App 手動退款、勾選確認，
  // 後端才反轉——否則客人已作廢卻仍被扣款。LINE Pay 由後端自動退、現金自錢櫃取出，皆不需此確認。
  const isMixed = sale.payment_method === "MIXED";
  const detail = useQuery({
    queryKey: ["sale-detail", sale.id, "void"],
    enabled: isMixed,
    queryFn: async () => {
      const { data, error: apiError } = await api.GET("/api/v1/sales/{sale_id}", {
        params: { path: { sale_id: sale.id } },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "讀取付款明細失敗");
      return data;
    },
  });
  const tenderTypes = new Set(
    detail.data?.tenders.map((tender) => tender.tender_type) ?? [],
  );
  const taiwanPayAmount =
    detail.data?.tenders.find((tender) => tender.tender_type === "TAIWAN_PAY")?.amount ?? null;
  const isTaiwanPay = sale.payment_method === "TAIWAN_PAY" || taiwanPayAmount !== null;
  const hasStoreCredit =
    sale.payment_method === "STORE_CREDIT" || tenderTypes.has("STORE_CREDIT");
  const hasLinePay =
    sale.payment_method === "LINE_PAY" || tenderTypes.has("LINE_PAY");
  const hasCash = sale.payment_method === "CASH" || tenderTypes.has("CASH");
  const paymentDetailPending = isMixed && detail.isLoading;
  const [manualRefundAck, setManualRefundAck] = useState(false);
  const voidSale = useMutation({
    mutationFn: async () => {
      const { data, error: apiError } = await api.POST("/api/v1/sales/{sale_id}/void", {
        params: {
          path: { sale_id: sale.id },
          query: isTaiwanPay ? { manual_refund_ack: manualRefundAck } : {},
        },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "作廢失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onVoided();
    },
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="作廢銷售確認"
    >
      <div className="card pos-dialog">
        <h2>作廢銷售 #{sale.id}？</h2>
        <p>
          總額 <span className="money">${formatNtd(parseNtd(sale.total) ?? 0)}</span>
          ，作廢後庫存回補、點數與購物金沖回、寄售結算反轉，且無法復原。
        </p>
        {paymentDetailPending ? (
          <p className="hint">載入付款明細中…</p>
        ) : isTaiwanPay ? (
          <>
            <p className="hint">
              此單包含台灣Pay 收款
              {taiwanPayAmount !== null
                ? ` $${formatNtd(parseNtd(taiwanPayAmount) ?? 0)}`
                : ""}
              （無 API）：請先於台灣Pay App 手動退款給客人，再勾選下方確認。
            </p>
            <label className="field field-toggle">
              <input
                type="checkbox"
                name="manual_refund_ack"
                checked={manualRefundAck}
                onChange={(e) => setManualRefundAck(e.target.checked)}
              />
              <span className="field-label">我已於台灣Pay App 完成退款給客人</span>
            </label>
          </>
        ) : detail.isError ? null : (
          <p className="hint">
            {hasStoreCredit && "購物金將回補原會員餘額。"}
            {hasLinePay && "LINE Pay 將由系統自動原路退款。"}
            {hasCash && "現金請直接自錢櫃退還，關帳對帳會核對差異。"}
          </p>
        )}
        {detail.isError && (
          <p role="alert" className="form-error">
            讀取付款明細失敗，請重試後再作廢。
          </p>
        )}
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button
            type="button"
            className="btn-danger"
            onClick={() => voidSale.mutate()}
            disabled={
              voidSale.isPending ||
              paymentDetailPending ||
              detail.isError ||
              (isTaiwanPay && !manualRefundAck)
            }
          >
            {voidSale.isPending ? "作廢中…" : "確認作廢"}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

function ReturnDialog({
  sale,
  onClose,
  onReturned,
}: {
  sale: SaleSummary;
  onClose: () => void;
  onReturned: (refund: number, tenders: ReturnTenderRead[]) => void;
}) {
  const [qtys, setQtys] = useState<Record<number, number>>({});
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [taiwanPayRefundConfirmed, setTaiwanPayRefundConfirmed] = useState(false);
  // 冪等鍵綁定「一次退貨嘗試」：回應遺失後從錯誤重試，必須沿用同鍵才觸發後端 replay、不重複
  // 退款/回補/沖點（Codex P1）。**持久化跨對話框重掛/重整（Codex 第二輪 #3）**：LINE Pay 退款
  // 於本地 commit 前呼叫平台，若之後失敗/崩潰，關開對話框或重整會換出新鍵而繞過 durable 退款
  // 日誌重複退款。故以「該銷售 + 退貨計畫指紋」為界持久化鍵：同計畫（含重掛/重試）恆同鍵→後端
  // replay 或 durable 日誌 SUCCEEDED 跳過，不重退；改計畫→新鍵→新退貨。鍵於送出時取（見 mutationFn）。
  const idemScope = `return-${sale.id}`;
  const planFingerprintOf = (q: Record<number, number>, r: string): string =>
    `${JSON.stringify(q)}|${r.trim()}`;
  const detail = useQuery({
    queryKey: ["sale-detail", sale.id],
    queryFn: async () => {
      const { data, error: apiError } = await api.GET("/api/v1/sales/{sale_id}", {
        params: { path: { sale_id: sale.id } },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "讀取銷售明細失敗");
      return data;
    },
  });
  const lines = detail.data?.lines ?? [];
  // 只列還有可退餘量的行（全退的不再出現，避免可選卻被後端 409）
  const returnable = lines.filter((l) => isReturnable(l) && remainingQty(l) > 0);
  const refund = computeRefund(lines, qtys);
  const tenders = detail.data?.tenders ?? [];
  const tenderTypes = new Set(tenders.map((tender) => tender.tender_type));
  const refundPolicy = tenderTypes.has("STORE_CREDIT")
    ? "退款會先回補購物金，再退回原本的現金、LINE Pay 或台灣Pay；"
    : "退款會退回原付款方式；";
  const previousRefund = lines.reduce(
    (sum, line) =>
      sum + (parseNtd(line.unit_price) ?? 0) * (line.returned_qty ?? 0),
    0,
  );
  const predictedRefund = refundPlan(tenders, previousRefund, refund);
  const refundSupported = detail.isSuccess && supportsRefund(tenders);
  const hasTaiwanPayRefund = predictedRefund.some(
    (leg) => leg.tender_type === "TAIWAN_PAY",
  );

  const submit = useMutation({
    mutationFn: async () => {
      const invalid = validateReturnPlan(lines, qtys, reason);
      if (invalid) throw new Error(invalid);
      // 持久化冪等鍵（Codex 第二輪 #3）：同銷售同退貨計畫恆得同鍵，跨對話框重掛/重整存活。
      const idemKey = getOrCreatePersistedIdemKey(
        idemScope,
        planFingerprintOf(qtys, reason),
      );
      const { data, error: apiError } = await api.POST("/api/v1/returns", {
        params: { header: { "Idempotency-Key": idemKey } },
        body: {
          sale_id: sale.id,
          reason: reason.trim(),
          lines: Object.entries(qtys)
            .filter(([, q]) => q > 0)
            .map(([id, q]) => ({ sale_line_id: Number(id), qty: q })),
          taiwan_pay_refund_confirmed: taiwanPayRefundConfirmed,
        },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "退貨失敗");
      return data;
    },
    onSuccess: (data) => {
      clearPersistedIdemKey(idemScope); // 退貨成立 → 清鍵，下次換新鍵
      onReturned(parseNtd(data.refund_amount) ?? 0, data.refund_tenders);
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="退貨">
      <div className="card pos-dialog" style={{ maxWidth: 560 }}>
        <h2>退貨 #{sale.id}</h2>
        <p className="hint">
          {refundPolicy}庫存與會員點數會同步調整。餐飲品項不支援退貨。
        </p>
        {detail.isLoading && <p>載入明細中…</p>}
        {detail.isError && (
          <p role="alert" className="form-error">
            讀取銷售明細失敗。{" "}
            <button type="button" onClick={() => void detail.refetch()}>
              重試
            </button>
          </p>
        )}
        {detail.isSuccess && !refundSupported && (
          <p role="alert" className="form-error">
            此單包含多種外部付款渠道，系統無法安全判定退款順序，請聯繫管理者。
          </p>
        )}
        {refundSupported && returnable.length > 0 && (
          <>
            <div className="return-dialog-toolbar">
              <span className="hint">可逐項調整，也可一次帶入全部可退數量。</span>
              <button
                type="button"
                className="btn-ghost"
                onClick={() =>
                  setQtys(
                    Object.fromEntries(returnable.map((line) => [line.id, remainingQty(line)])),
                  )
                }
              >
                整筆退貨
              </button>
            </div>
            <table className="data-table return-lines-table">
            <thead>
              <tr>
                <th>品項</th>
                <th>單價</th>
                <th>可退餘量</th>
                <th>退貨數</th>
              </tr>
            </thead>
            <tbody>
              {returnable.map((line) => {
                const remaining = remainingQty(line);
                return (
                  <tr key={line.id}>
                    <td>{line.description}</td>
                    <td>${formatNtd(parseNtd(line.unit_price) ?? 0)}</td>
                    <td>
                      {remaining}
                      {line.returned_qty ? `（原 ${line.qty}、已退 ${line.returned_qty}）` : ""}
                    </td>
                    <td>
                      <input
                        className="return-qty-input"
                        type="number"
                        min={0}
                        max={remaining}
                        value={qtys[line.id] ?? 0}
                        aria-label={`${line.description} 退貨數量`}
                        onChange={(e) =>
                          setQtys((prev) => ({
                            ...prev,
                            [line.id]: Math.max(
                              0,
                              Math.min(remaining, Math.floor(Number(e.target.value) || 0)),
                            ),
                          }))
                        }
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
            </table>
          </>
        )}
        {detail.isSuccess && refundSupported && returnable.length === 0 && (
          <p className="hint">此單沒有可退貨的品項（餐飲不支援退貨）。</p>
        )}
        <label style={{ display: "block", marginTop: 12 }}>
          退貨原因{" "}
          <input
            type="text"
            value={reason}
            maxLength={200}
            style={{ width: "100%" }}
            onChange={(e) => setReason(e.target.value)}
            placeholder="例：尺寸不合／商品瑕疵"
          />
        </label>
        <p style={{ marginTop: 8 }}>
          預估退款 <span className="money">${formatNtd(refund)}</span>
        </p>
        {predictedRefund.length > 0 && (
          <div className="return-refund-preview" aria-label="預估退款去向">
            {predictedRefund.map((leg) => (
              <span key={leg.tender_type}>
                {refundTenderLabel[leg.tender_type]} <b>${formatNtd(leg.amount)}</b>
              </span>
            ))}
          </div>
        )}
        {hasTaiwanPayRefund && (
          <label className="field field-toggle return-taiwan-confirm">
            <input
              type="checkbox"
              checked={taiwanPayRefundConfirmed}
              onChange={(event) => setTaiwanPayRefundConfirmed(event.target.checked)}
            />
            <span className="field-label">
              已於台灣Pay完成退款 {formatNtd(
                predictedRefund.find((leg) => leg.tender_type === "TAIWAN_PAY")?.amount ?? 0,
              )} 元
            </span>
          </label>
        )}
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button
            type="button"
            className="btn-danger"
            disabled={
              submit.isPending ||
              refund <= 0 ||
              !refundSupported ||
              (hasTaiwanPayRefund && !taiwanPayRefundConfirmed)
            }
            onClick={() => {
              setError(null);
              submit.mutate();
            }}
          >
            {submit.isPending ? "退貨處理中…" : `確認退貨 $${formatNtd(refund)}`}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// LINE Pay 退款對帳（docs/30 finding #3）：結果未定（PENDING）的退款——店長於 LINE Pay 後台確認
// 實際是否退款後，於此標記已退款（SUCCEEDED）或未退款可重試（FAILED），解除卡住的退貨/作廢。
function LinePayReconcilePanel() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const pending = useQuery({
    queryKey: ["linepay-refunds", "pending"],
    queryFn: async () => {
      const { data, error: apiError } = await api.GET(
        "/api/v1/sales/linepay-refunds/pending",
      );
      if (!data) throw new Error(extractDetail(apiError) ?? "讀取未決退款失敗");
      return data;
    },
  });
  const resolve = useMutation({
    mutationFn: async (args: { id: number; resolution: "SUCCEEDED" | "FAILED" }) => {
      const { data, error: apiError } = await api.POST(
        "/api/v1/sales/linepay-refunds/{attempt_id}/resolve",
        {
          params: { path: { attempt_id: args.id } },
          body: { resolution: args.resolution },
        },
      );
      if (!data) throw new Error(extractDetail(apiError) ?? "解決失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["linepay-refunds", "pending"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const items = Array.isArray(pending.data) ? pending.data : [];
  if (items.length === 0) return null; // 無未決退款（或讀取中/失敗）→ 不顯示
  return (
    <div className="card" style={{ borderColor: "var(--danger, #b00)", marginBottom: "1rem" }}>
      <h2>LINE Pay 退款對帳（需處理）</h2>
      <p className="hint">
        以下退款結果未定（呼叫 LINE Pay 後崩潰或回應遺失）。請先至 LINE Pay
        後台確認該筆是否已退款，再於此標記——標記前該筆退貨/作廢會被擋下以免超退。
      </p>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <table className="data-table">
        <thead>
          <tr>
            <th>訂單號</th>
            <th>金額</th>
            <th>時間</th>
            <th>處理</th>
          </tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id}>
              <td>{a.order_id}</td>
              <td>${formatNtd(parseNtd(a.amount) ?? 0)}</td>
              <td>{formatTaipeiDateTime(a.created_at)}</td>
              <td>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={resolve.isPending}
                  onClick={() => resolve.mutate({ id: a.id, resolution: "SUCCEEDED" })}
                >
                  確認已退款
                </button>{" "}
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={resolve.isPending}
                  onClick={() => resolve.mutate({ id: a.id, resolution: "FAILED" })}
                >
                  確認未退款（可重試）
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SalesPage() {
  const isManager = useIsManager();
  const queryClient = useQueryClient();
  const [voidTarget, setVoidTarget] = useState<SaleSummary | null>(null);
  const [voidedNote, setVoidedNote] = useState<string | null>(null);
  const [returnTarget, setReturnTarget] = useState<SaleSummary | null>(null);
  const [signatureTaskId, setSignatureTaskId] = useState<number | null>(null);
  // 交易紀錄簽收（docs/23 K5b）：推 TRANSACTION_ACK 至手持裝置，客人核對後簽名留存（不擋流程）。
  const [ackNote, setAckNote] = useState<string | null>(null);
  const pushAck = useMutation({
    mutationFn: async (sale: SaleSummary) => {
      if (sale.buyer_contact_id == null) throw new Error("此單無買方會員，無法推送簽收");
      // content 由後端以銷售單為準重建（單號/總額/時間），客端不提供（Codex K5 第三輪：
      // 簽收證據不可由客端敘述）。
      const { data, error } = await api.POST("/api/v1/signing/tasks", {
        body: {
          kind: "TRANSACTION_ACK",
          contact_id: sale.buyer_contact_id,
          content: {},
          ref_type: "sale",
          ref_id: sale.id,
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "推送簽收失敗");
      return sale.id;
    },
    onSuccess: (saleId) => setAckNote(`已推送 #${saleId} 交易紀錄簽收至手持裝置`),
    onError: (e: Error) => setAckNote(e.message),
  });

  const sales = useQuery({
    queryKey: ["sales", "today"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/sales", {
        params: { query: { from: startOfTodayIso(), limit: 200 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取交易紀錄失敗");
      return data;
    },
  });

  const rows = sales.data ?? [];

  return (
    <section>
      <h1 className="page-title">交易紀錄（今日）</h1>
      <p className="hint">
        打錯單請在此作廢（限店長）。已退貨的單不可作廢，請走退貨流程處理剩餘部分。
      </p>
      {voidedNote !== null && <p className="form-success">{voidedNote}</p>}
      {ackNote !== null && <p className="hint">{ackNote}</p>}
      {isManager && <LinePayReconcilePanel />}
      {sales.isError && (
        <p role="alert" className="form-error">
          {(sales.error as Error).message}
        </p>
      )}
      {sales.isSuccess && rows.length === 0 && <p className="hint">今日尚無交易。</p>}
      {rows.length > 0 && (
        <div className="card">
          <table className="data-table sales-list">
          <thead>
            <tr>
              <th>時間</th>
              <th>單號</th>
              <th>總額</th>
              <th>發票狀態</th>
              <th>狀態</th>
              <th aria-label="簽收" />
              {isManager && <th aria-label="操作" />}
            </tr>
          </thead>
          <tbody>
            {rows.map((sale) => {
              const voided = sale.invoice_status === "VOID";
              const returned = sale.status === "RETURNED";
              return (
                <tr key={sale.id}>
                  <td>{timeLabel(sale.created_at)}</td>
                  <td>#{sale.id}</td>
                  <td>
                    <span className="money">${formatNtd(parseNtd(sale.total) ?? 0)}</span>
                  </td>
                  <td>{labelFor(INVOICE_STATUS_LABELS, sale.invoice_status)}</td>
                  <td>{voided ? "已作廢" : labelFor(SALE_STATUS_LABELS, sale.status)}</td>
                  <td>
                    {!voided && !returned && (
                      <button
                        type="button"
                        className="btn-ghost"
                        aria-label={`退貨銷售 ${sale.id}`}
                        onClick={() => {
                          setVoidedNote(null);
                          setReturnTarget(sale);
                        }}
                      >
                        退貨
                      </button>
                    )}
                    {!voided && !returned && sale.buyer_contact_id != null && (
                      <button
                        type="button"
                        className="btn-ghost"
                        aria-label={`推送銷售 ${sale.id} 簽收`}
                        disabled={pushAck.isPending}
                        onClick={() => {
                          setAckNote(null);
                          pushAck.mutate(sale);
                        }}
                      >
                        推送簽收
                      </button>
                    )}
                    {sale.signature_task_id != null && (
                      <button
                        type="button"
                        className="btn-ghost"
                        aria-label={`查看銷售 ${sale.id} 簽名`}
                        onClick={() => setSignatureTaskId(sale.signature_task_id)}
                      >
                        查看簽名
                      </button>
                    )}
                  </td>
                  {isManager && (
                    <td>
                      {!voided && !returned && (
                        <button
                          type="button"
                          className="btn-danger"
                          aria-label={`作廢銷售 ${sale.id}`}
                          onClick={() => {
                            setVoidedNote(null);
                            setVoidTarget(sale);
                          }}
                        >
                          作廢
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
          </table>
        </div>
      )}
      {returnTarget !== null && (
        <ReturnDialog
          sale={returnTarget}
          onClose={() => setReturnTarget(null)}
          onReturned={(refund, tenders) => {
            const split = tenders
              .map(
                (tender) =>
                  `${refundTenderLabel[tender.tender_type]} $${formatNtd(parseNtd(tender.amount) ?? 0)}`,
              )
              .join("、");
            setVoidedNote(
              `銷售 #${returnTarget.id} 退貨完成，共 $${formatNtd(refund)}：${split}。`,
            );
            setReturnTarget(null);
            void queryClient.invalidateQueries({ queryKey: ["sales", "today"] });
          }}
        />
      )}
      {voidTarget !== null && (
        <VoidConfirmDialog
          sale={voidTarget}
          onClose={() => setVoidTarget(null)}
          onVoided={() => {
            setVoidedNote(`銷售 #${voidTarget.id} 已作廢。`);
            setVoidTarget(null);
            void queryClient.invalidateQueries({ queryKey: ["sales", "today"] });
          }}
        />
      )}
      {signatureTaskId !== null && (
        <SignatureEvidenceDialog
          taskId={signatureTaskId}
          onClose={() => setSignatureTaskId(null)}
        />
      )}
    </section>
  );
}
