"use client";
// /sales 交易紀錄（當日）：打錯單的現場救援入口——列出今日銷售、店長可作廢（二次確認，
// docs/10 §28 危險動作）。作廢由後端整套反轉：庫存回補、點數/購物金沖回、寄售結算反轉、
// 電子發票中止；已退貨/已作廢的單後端會擋（409），前端先行停用按鈕。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { INVOICE_STATUS_LABELS, labelFor } from "@/features/member/labels";
import { terminalInstallationId } from "@/features/customer-display/PosCustomerDisplay";
import { printKitchenTicket } from "@/lib/agent";
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
  computePreviousRefund,
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
import {
  invoiceActionLabel,
  returnSubmitBlockers,
} from "@/features/returns/invoice-consent";

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
  VOIDED: "已作廢",
};

function ManualInvoiceDialog({
  sale,
  onClose,
  onRegistered,
}: {
  sale: SaleSummary;
  onClose: () => void;
  onRegistered: () => void;
}) {
  // 登記手開紙本備用發票（docs/36）：字軌用完/平台故障時店家改開紙本，這裡把那張紙
  // 登記進系統，並取消待送的開立——否則字軌恢復後重試會讓平台再開一張。
  const [invoiceNo, setInvoiceNo] = useState("");
  const [invoiceDate, setInvoiceDate] = useState(() => taipeiDate());
  const [invoiceTime, setInvoiceTime] = useState("");
  const [randomNumber, setRandomNumber] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const register = useMutation({
    mutationFn: async () => {
      const { data, error: apiError } = await api.POST(
        "/api/v1/einvoice/sales/{sale_id}/manual-invoice",
        {
          params: { path: { sale_id: sale.id } },
          body: {
            invoice_no: invoiceNo.trim().toUpperCase(),
            invoice_date: invoiceDate,
            invoice_time: invoiceTime === "" ? null : `${invoiceTime}:00`,
            random_number: randomNumber === "" ? null : randomNumber,
            total: sale.total,
            note: note.trim() === "" ? null : note.trim(),
          },
        },
      );
      if (!data) throw new Error(extractDetail(apiError) ?? "登記失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onRegistered();
    },
    onError: (err: Error) => setError(err.message),
  });

  const invoiceNoBad =
    invoiceNo !== "" && !/^[A-Z]{2}\d{8}$/.test(invoiceNo.trim().toUpperCase());
  const randomBad = randomNumber !== "" && !/^\d{4}$/.test(randomNumber);

  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="登記手開發票"
    >
      <div className="card pos-dialog">
        <h2>登記手開發票 #{sale.id}</h2>
        <p className="hint">
          把客人手上那張<strong>紙本備用發票</strong>的號碼登記進系統。登記後本筆不會再自動開立
          電子發票（避免同一筆交易開出兩張），日後的作廢與折讓也須依國稅局程序以紙本辦理。
        </p>
        <p>
          本筆金額{" "}
          <span className="money">${formatNtd(parseNtd(sale.total) ?? 0)}</span>
          ，登記不會更動金額。
        </p>
        <label className="field">
          <span className="field-label">發票號碼（字軌 2 碼 + 8 位數）</span>
          <input
            aria-label="發票號碼"
            value={invoiceNo}
            placeholder="ZA10029999"
            onChange={(e) => setInvoiceNo(e.target.value)}
          />
          {invoiceNoBad && <span className="form-error">格式須為 2 個大寫英文字母 + 8 位數字</span>}
        </label>
        <label className="field">
          <span className="field-label">開立日期</span>
          <input
            type="date"
            aria-label="開立日期"
            value={invoiceDate}
            onChange={(e) => setInvoiceDate(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">開立時間（選填）</span>
          <input
            type="time"
            aria-label="開立時間"
            value={invoiceTime}
            onChange={(e) => setInvoiceTime(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">隨機碼（選填，4 位數）</span>
          <input
            aria-label="隨機碼"
            inputMode="numeric"
            value={randomNumber}
            onChange={(e) => setRandomNumber(e.target.value)}
          />
          {randomBad && <span className="form-error">隨機碼須為 4 位數字</span>}
        </label>
        <label className="field">
          <span className="field-label">事由（選填，供日後稽核追溯）</span>
          <input
            aria-label="事由"
            value={note}
            placeholder="字軌用完 / 平台故障"
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button
            type="button"
            className="btn-primary"
            disabled={
              register.isPending ||
              invoiceNo.trim() === "" ||
              invoiceNoBad ||
              randomBad ||
              invoiceDate === ""
            }
            onClick={() => register.mutate()}
          >
            {register.isPending ? "登記中…" : "確認登記"}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

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
  // 手開紙本（docs/36）：來源由列表 API 帶入，**必須在畫面顯示任何退款指示之前**就知道，
  // 否則店員會先照指示退款、送出後才被後端擋下——錢已經出去了。
  const isManualPaper = sale.invoice_issue_channel === "MANUAL_PAPER";
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
        {isManualPaper ? (
          <>
            {/* 手開紙本（docs/36）：**必須在顯示任何退款指示之前**擋下。若照常走一般流程，
                台灣Pay 的路徑會先叫店員去 App 把錢退給客人、勾確認再送出，後端這時才回 409
                ——錢已經出去、單子卻還有效（Codex 對抗審查第三輪 critical）。 */}
            <p role="alert" className="form-error">
              本筆為手開紙本發票，系統不代管作廢。請依國稅局程序作廢紙本並保留收回聯；
              作廢完成前請勿退款給客人。
            </p>
            <div className="pos-dialog-actions">
              <button type="button" className="btn-primary" onClick={onClose}>
                知道了
              </button>
            </div>
          </>
        ) : (
          <>
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
          </>
        )}
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
  // 贈品不一併收回時的說明（有未退贈品且退了主商品時必填；後端亦擋，雙重防線）。
  const [unreturnedGiftNote, setUnreturnedGiftNote] = useState("");
  // 發票處置（作廢／折讓）的兩道前置：收回紙本證明聯、買受人簽名同意。兩者都綁定當下的
  // 退貨計畫——改了要退什麼，先前的確認/同意即失效（以計畫指紋比對，不另用 effect 清狀態）。
  const [paperRecalledPlanKey, setPaperRecalledPlanKey] = useState<string | null>(null);
  const [consentTaskId, setConsentTaskId] = useState<number | null>(null);
  const [consentPlanKey, setConsentPlanKey] = useState<string | null>(null);
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
  // 預估值（送出前先顯示）；後端預覽回來後一律改用它的 refund_total——金額只有一個權威來源。
  const estimatedRefund = computeRefund(lines, qtys);
  const tenders = detail.data?.tenders ?? [];
  const tenderTypes = new Set(tenders.map((tender) => tender.tender_type));
  const refundPolicy = tenderTypes.has("STORE_CREDIT")
    ? "退款會先回補購物金，再退回原本的現金、LINE Pay 或台灣Pay；"
    : "退款會退回原付款方式；";
  const previousRefund = computePreviousRefund(lines);
  const refundSupported = detail.isSuccess && supportsRefund(tenders);

  // 本次要退的明細（送預覽、建同意任務、送出退貨三處同一份，避免三邊不一致）。
  const returnLines = Object.entries(qtys)
    .filter(([, q]) => q > 0)
    .map(([id, q]) => ({ sale_line_id: Number(id), qty: q }))
    .sort((a, b) => a.sale_line_id - b.sale_line_id);
  const planKey = JSON.stringify(returnLines);
  const consentMatchesPlan = consentTaskId !== null && consentPlanKey === planKey;
  const paperRecalled = paperRecalledPlanKey === planKey;

  const preview = useQuery({
    queryKey: ["return-preview", sale.id, planKey],
    enabled: returnLines.length > 0,
    queryFn: async () => {
      const { data, error: apiError } = await api.POST("/api/v1/returns/preview", {
        body: { sale_id: sale.id, lines: returnLines },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "讀取發票處置預覽失敗");
      return data;
    },
  });
  const previewData = returnLines.length > 0 ? (preview.data ?? null) : null;
  // 退款金額的權威來源是後端預覽；預覽尚未回來時先顯示本機預估（送出仍以後端為準）。
  const refund =
    previewData !== null
      ? (parseNtd(previewData.refund_total) ?? estimatedRefund)
      : estimatedRefund;
  // 本單還沒收回的贈品：退了主商品卻不收回贈品，店員必須明確說明原因（系統不自行假設）。
  const unreturnedGifts = previewData?.unreturned_gifts ?? [];
  const returningNonGift = lines.some(
    (line) => (qtys[line.id] ?? 0) > 0 && line.line_kind !== "GIFT",
  );
  const needsGiftDecision = unreturnedGifts.length > 0 && returningNonGift;
  const predictedRefund = refundPlan(tenders, previousRefund, refund);
  const hasTaiwanPayRefund = predictedRefund.some(
    (leg) => leg.tender_type === "TAIWAN_PAY",
  );

  const consentTask = useQuery({
    queryKey: ["signing-task", consentTaskId],
    enabled: consentTaskId != null,
    refetchInterval: (q) =>
      q.state.data?.status === "PENDING" || q.state.data?.status === "SIGNING" ? 2000 : false,
    queryFn: async () => {
      if (consentTaskId == null) return null;
      const { data } = await api.GET("/api/v1/signing/tasks/{task_id}", {
        params: { path: { task_id: consentTaskId } },
      });
      return data ?? null;
    },
  });
  const consentSigned = consentMatchesPlan && consentTask.data?.status === "SIGNED";

  const pushConsent = useMutation({
    mutationFn: async () => {
      const terminalResponse = await api.POST("/api/v1/customer-display/terminals", {
        body: { installation_id: terminalInstallationId(), name: "主要櫃檯" },
      });
      const terminal = terminalResponse.data;
      if (!terminal?.paired_kiosk) throw new Error("請先將此 POS 櫃檯與顧客螢幕配對");
      if (!terminal.paired_kiosk.online) {
        throw new Error("顧客螢幕目前離線，無法請客人簽名同意");
      }
      // 同意書內容由後端依銷售單與發票政策重建；客端只送「退哪些、退幾件」。
      // contact_id 留空：臨櫃非會員也要能簽（有會員時帶入，證據可標明簽署人）。
      const { data, error: apiError } = await api.POST("/api/v1/signing/tasks", {
        body: {
          kind: "RETURN_INVOICE_CONSENT",
          contact_id: sale.buyer_contact_id ?? null,
          content: { lines: returnLines },
          terminal_id: terminal.id,
          ref_type: "sale",
          ref_id: sale.id,
        },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "推送簽名同意失敗");
      return data.id;
    },
    onSuccess: (taskId) => {
      setError(null);
      setConsentTaskId(taskId);
      setConsentPlanKey(planKey);
    },
    onError: (e: Error) => setError(e.message),
  });

  const blockers = returnSubmitBlockers(previewData, {
    paperRecalled,
    consentTaskSigned: consentSigned,
  });

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
          lines: returnLines,
          taiwan_pay_refund_confirmed: taiwanPayRefundConfirmed,
          invoice_recalled: paperRecalled,
          consent_signature_task_id: consentSigned ? consentTaskId : null,
          unreturned_gift_note:
            unreturnedGiftNote.trim() === "" ? null : unreturnedGiftNote.trim(),
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
                {/* 退款依**實付**計算，牌價不等於實付時要讓店員一眼看見差別。 */}
                <th>本行實付</th>
                <th>可退餘量</th>
                <th>退貨數</th>
              </tr>
            </thead>
            <tbody>
              {returnable.map((line) => {
                const remaining = remainingQty(line);
                return (
                  <tr key={line.id}>
                    <td>
                      {line.description}
                      {line.line_kind === "GIFT" && (
                        <span className="pos-gift-badge">贈品</span>
                      )}
                    </td>
                    <td>${formatNtd(parseNtd(line.unit_price) ?? 0)}</td>
                    <td>${formatNtd(parseNtd(line.net_amount) ?? 0)}</td>
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
        {needsGiftDecision && (
          <div className="return-gift-notice">
            <p role="alert" className="form-error">
              本單有贈品未一併退回：
              {unreturnedGifts
                .map((gift) => `${gift.description} × ${gift.qty}`)
                .join("、")}
              （原價 $
              {formatNtd(
                unreturnedGifts.reduce(
                  (sum, gift) => sum + (parseNtd(gift.retail_value) ?? 0),
                  0,
                ),
              )}
              ）
            </p>
            <p className="hint">
              請一併勾選退回，或說明不收回的原因（會寫入稽核紀錄）。
            </p>
            <input
              type="text"
              value={unreturnedGiftNote}
              maxLength={500}
              style={{ width: "100%" }}
              aria-label="贈品不收回的原因"
              onChange={(e) => setUnreturnedGiftNote(e.target.value)}
              placeholder="例：贈品已拆封無法回售，經客人同意不收回"
            />
          </div>
        )}
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
        {previewData !== null && previewData.invoice_action !== "NONE" && (
          <section className="return-invoice-notice" aria-label="發票處置">
            <p className="return-invoice-action">
              本次退貨將
              <b>{invoiceActionLabel(previewData.invoice_action)}</b>
            </p>
            <p className="hint">{previewData.reason}</p>
            {previewData.requires_paper_recall && (
              <label className="field field-toggle return-paper-recall">
                <input
                  type="checkbox"
                  checked={paperRecalled}
                  onChange={(event) =>
                    setPaperRecalledPlanKey(event.target.checked ? planKey : null)
                  }
                />
                <span className="field-label">已向客人收回發票證明聯（紙本）</span>
              </label>
            )}
            {previewData.requires_customer_consent && (
              <div className="return-consent">
                {consentSigned ? (
                  <p className="form-success">客人已簽名同意（簽署單號 #{consentTaskId}）</p>
                ) : (
                  <>
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={pushConsent.isPending}
                      onClick={() => pushConsent.mutate()}
                    >
                      {pushConsent.isPending ? "推送中…" : "請客人於顧客螢幕簽名同意"}
                    </button>
                    {consentMatchesPlan && consentTask.data?.status === "PENDING" && (
                      <span className="hint">已送出，等待客人簽名…</span>
                    )}
                    {consentMatchesPlan && consentTask.data?.status === "SIGNING" && (
                      <span className="hint">客人簽名中…</span>
                    )}
                    {consentTaskId !== null && !consentMatchesPlan && (
                      <span className="hint">退貨品項已變更，請重新請客人簽名。</span>
                    )}
                  </>
                )}
              </div>
            )}
          </section>
        )}
        {preview.isError && returnLines.length > 0 && (
          <p role="alert" className="form-error">
            無法確認本次退貨的發票處置方式，請重試。{" "}
            <button type="button" onClick={() => void preview.refetch()}>
              重試
            </button>
          </p>
        )}
        {blockers.map((blocker) => (
          <p key={blocker} className="hint return-blocker">
            {blocker}
          </p>
        ))}
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
              // 退款 0 元是合法的（純贈品退回），所以擋的是「什麼都沒選」而不是金額。
              returnLines.length === 0 ||
              (needsGiftDecision && unreturnedGiftNote.trim() === "") ||
              !refundSupported ||
              (hasTaiwanPayRefund && !taiwanPayRefundConfirmed) ||
              preview.isFetching ||
              preview.isError ||
              blockers.length > 0
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
  // 出餐單重印（docs/35 §3.2 要求的第二個入口）：店員離開 POS 完成頁後，吧台若沒收到單
  // ——代理離線、缺紙、單子被丟掉——這裡是唯一補得回來的地方。
  // 列表只有摘要（無明細），故先抓完整銷售再送代理。
  const [kitchenNote, setKitchenNote] = useState<string | null>(null);
  const reprintKitchen = useMutation({
    mutationFn: async (sale: SaleSummary) => {
      if (sale.service_mode == null) throw new Error("此單沒有餐飲品項，無需出餐單");
      const { data, error } = await api.GET("/api/v1/sales/{sale_id}", {
        params: { path: { sale_id: sale.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取銷售明細失敗");
      await printKitchenTicket(data, sale.service_mode, sale.table_no ?? null);
      return sale.id;
    },
    onSuccess: (saleId) => setKitchenNote(`已送出 #${saleId} 的出餐單。`),
    onError: (err: Error) => setKitchenNote(err.message),
  });

  const pushAck = useMutation({
    mutationFn: async (sale: SaleSummary) => {
      if (sale.buyer_contact_id == null) throw new Error("此單無買方會員，無法推送簽收");
      const terminalResponse = await api.POST("/api/v1/customer-display/terminals", {
        body: {
          installation_id: terminalInstallationId(),
          name: "主要櫃檯",
        },
      });
      const terminal = terminalResponse.data;
      if (!terminal?.paired_kiosk) {
        throw new Error("請先將此 POS 櫃檯與顧客螢幕配對");
      }
      if (!terminal.paired_kiosk.online) {
        throw new Error("顧客螢幕目前離線，無法推送交易簽收");
      }
      // content 由後端以銷售單為準重建（單號/總額/時間），客端不提供（Codex K5 第三輪：
      // 簽收證據不可由客端敘述）。
      const { data, error } = await api.POST("/api/v1/signing/tasks", {
        body: {
          kind: "TRANSACTION_ACK",
          contact_id: sale.buyer_contact_id,
          content: {},
          terminal_id: terminal.id,
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

  // 「只看未開立」（docs/36）：開立失敗的單一旦離開 POS 完成畫面就再也找不到
  // ——前端沒有發票佇列頁，這個清單是把它們撈回來的唯一途徑。
  // **由後端以實際發票狀態判定資格且不限日期**：只查今日會讓昨天沒收斂的單永遠消失；
  // 客端從 sale.invoice_status 推導則會把「電子發票關閉、根本沒有發票」的單也列進來，
  // 按下去只會 404。切換時 queryKey 必須跟著換，否則會沿用另一模式的快取。
  const [pendingInvoiceOnly, setPendingInvoiceOnly] = useState(false);
  const sales = useQuery({
    queryKey: ["sales", pendingInvoiceOnly ? "registerable" : "today"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/sales", {
        params: {
          query: pendingInvoiceOnly
            ? { invoice_registerable: true, limit: 200 }
            : { from: startOfTodayIso(), limit: 200 },
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取交易紀錄失敗");
      return data;
    },
  });
  const [manualInvoiceTarget, setManualInvoiceTarget] = useState<SaleSummary | null>(null);
  const rows = sales.data ?? [];

  return (
    <section>
      <h1 className="page-title">交易紀錄（今日）</h1>
      <p className="hint">
        打錯單請在此作廢（限店長）。已退貨的單不可作廢，請走退貨流程處理剩餘部分。
      </p>
      {voidedNote !== null && <p className="form-success">{voidedNote}</p>}
      {ackNote !== null && <p className="hint">{ackNote}</p>}
      {kitchenNote !== null && (
        <p className={reprintKitchen.isError ? "form-error" : "hint"}>{kitchenNote}</p>
      )}
      <label className="field field-toggle sales-invoice-filter">
        <input
          type="checkbox"
          checked={pendingInvoiceOnly}
          onChange={(e) => setPendingInvoiceOnly(e.target.checked)}
        />
        <span className="field-label">
          只看未開立發票的交易（可登記手開；不限今日）
        </span>
      </label>
      {isManager && <LinePayReconcilePanel />}
      {sales.isError && (
        <p role="alert" className="form-error">
          {(sales.error as Error).message}
        </p>
      )}
      {sales.isSuccess && rows.length === 0 && <p className="hint">今日尚無交易。</p>}
      {rows.length > 0 && (
        <div className="card sales-list-card">
          <div className="sales-list-wrap">
          <table className="data-table sales-list">
          <thead>
            <tr>
              <th>時間</th>
              <th>單號</th>
              <th>總額</th>
              <th>桌號</th>
              <th>發票狀態</th>
              <th>狀態</th>
              <th aria-label="簽收" />
              {isManager && <th aria-label="操作" />}
            </tr>
          </thead>
          <tbody>
            {rows.map((sale) => {
              // 「這筆銷售是否作廢」看 sale.status——invoice_status 是**發票**的狀態，
              // 未啟用電子發票時根本沒有發票，兩者語意不同（見 ADR-013）。
              const voided = sale.status === "VOIDED";
              const returned = sale.status === "RETURNED";
              return (
                <tr key={sale.id}>
                  <td>{timeLabel(sale.created_at)}</td>
                  <td>#{sale.id}</td>
                  <td>
                    <span className="money">${formatNtd(parseNtd(sale.total) ?? 0)}</span>
                  </td>
                  {/* 餐飲內用/外帶（docs/35）：無餐飲的單顯示「—」，外帶顯示「外帶」。 */}
                  <td>
                    {sale.service_mode === "DINE_IN"
                      ? (sale.table_no ?? "—")
                      : sale.service_mode === "TAKEOUT"
                        ? "外帶"
                        : "—"}
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
                    {isManager && !voided && sale.invoice_status === "PENDING_ISSUE" && (
                      <button
                        type="button"
                        className="btn-ghost"
                        aria-label={`登記銷售 ${sale.id} 的手開發票`}
                        onClick={() => {
                          setVoidedNote(null);
                          setManualInvoiceTarget(sale);
                        }}
                      >
                        登記手開發票
                      </button>
                    )}
                    {sale.service_mode != null && !voided && (
                      <button
                        type="button"
                        className="btn-ghost"
                        aria-label={`重印銷售 ${sale.id} 出餐單`}
                        disabled={reprintKitchen.isPending}
                        onClick={() => {
                          setKitchenNote(null);
                          reprintKitchen.mutate(sale);
                        }}
                      >
                        重印出餐單
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
      {manualInvoiceTarget !== null && (
        <ManualInvoiceDialog
          sale={manualInvoiceTarget}
          onClose={() => setManualInvoiceTarget(null)}
          onRegistered={() => {
            setVoidedNote(
              `#${manualInvoiceTarget.id} 已登記手開發票；本筆不會再自動開立電子發票。`,
            );
            setManualInvoiceTarget(null);
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
