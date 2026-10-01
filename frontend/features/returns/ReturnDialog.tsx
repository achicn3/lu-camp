"use client";
// 退貨／餐點退款對話框（交易紀錄頁與餐飲交易紀錄頁共用，docs/47）。
//
// 兩個頁面退的是同一張單的不同部分，但**退貨引擎只有一個**：金額（差額法）、退款去向、
// 發票處置、買受人同意、台灣Pay 手動退款、LINE Pay 防重退都在後端算、這裡只呈現。
// 退款去向一律照後端預覽顯示——餐點只退外部付款、二手購物金優先這些規則只在後端維護。
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { terminalInstallationId } from "@/features/customer-display/PosCustomerDisplay";
import {
  computePreviousRefund,
  computeRefund,
  isReturnable,
  remainingQty,
  type ReturnScope,
  validateReturnPlan,
} from "@/features/returns/plan";
import {
  type RefundLeg,
  type RefundTenderType,
  refundPlan,
  refundTenderLabel,
  supportsRefund,
} from "@/features/returns/refund";
import {
  invoiceActionLabel,
  mayShowExternalRefundInstructions,
  returnSubmitBlockers,
} from "@/features/returns/invoice-consent";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { clearPersistedIdemKey, getOrCreatePersistedIdemKey } from "@/lib/idempotency";
import { formatNtd, parseNtd } from "@/lib/money";

type ReturnTenderRead = components["schemas"]["ReturnTenderRead"];

/** 對話框需要的銷售資訊（交易紀錄與餐飲交易紀錄的列都帶得出來）。 */
export interface ReturnDialogSale {
  id: number;
  buyer_contact_id: number | null;
  /** 手開紙本（docs/36）的提示；預覽回來前才用得到，沒有就給 null。 */
  invoice_issue_channel?: string | null;
}

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export function ReturnDialog({
  sale,
  onClose,
  onReturned,
  canConfirmPaper,
  scope = "goods",
}: {
  sale: ReturnDialogSale;
  onClose: () => void;
  onReturned: (refund: number, tenders: ReturnTenderRead[]) => void;
  /** 店長才可確認「手開紙本已處置」（後端亦以 403 擋）。 */
  canConfirmPaper: boolean;
  /** 這個對話框能退哪一類（docs/47）：二手／一般商品，或餐點。兩邊共用同一個退貨引擎。 */
  scope?: ReturnScope;
}) {
  const [qtys, setQtys] = useState<Record<number, number>>({});
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  // 台灣Pay 的「已完成退款」必須**綁定當下的退貨計畫**（同紙本收回的做法）。
  // 原本是整個對話框共用的 boolean：店員先退 300 元勾確認，再把數量改多，
  // 勾勾仍為 true → 系統照新金額建立退貨，**認定的退款額大於實際退出去的錢**
  // （Codex 對抗審查第五輪 high；既有問題，非本功能引入）。
  const [taiwanPayConfirmedPlanKey, setTaiwanPayConfirmedPlanKey] = useState<string | null>(
    null,
  );
  // 手開紙本（docs/36）：這筆的發票平台上不存在，系統不代開折讓/作廢。店長確認已依
  // 國稅局程序處置紙本後才可退貨，且只做本地反轉。不給這條路的話，開過紙本發票的單
  // 就**永遠退不了**——庫存、退款、點數、寄售結算全部反轉不了。
  // 「紙本已處置」限店長（後端亦以 403 擋）：店員看到勾選框只會勾了才發現不能送。
  const [manualPaperDisposedPlanKey, setManualPaperDisposedPlanKey] = useState<string | null>(
    null,
  );
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
  const returnable = lines.filter((l) => isReturnable(l, scope) && remainingQty(l) > 0);
  // 預估值（送出前先顯示）；後端預覽回來後一律改用它的 refund_total——金額只有一個權威來源。
  const estimatedRefund = computeRefund(lines, qtys);
  const tenders = detail.data?.tenders ?? [];
  const tenderTypes = new Set(tenders.map((tender) => tender.tender_type));
  const refundPolicy =
    scope === "food"
      ? "餐點退款退回原本的現金、LINE Pay 或台灣Pay（餐點不能用購物金付，也不會退成購物金）。"
      : tenderTypes.has("STORE_CREDIT")
        ? "退款會先回補購物金，再退回原本的現金、LINE Pay 或台灣Pay；"
        : "退款會退回原付款方式；";
  const previousRefund = computePreviousRefund(lines);
  // 付款組合能不能退：預覽回來後以後端判定為準（同一支函式決定實際退款去向）。
  const refundSupported = detail.isSuccess && supportsRefund(tenders);

  // 本次要退的明細（送預覽、建同意任務、送出退貨三處同一份，避免三邊不一致）。
  // 餐點「這份還能賣」（docs/47 §3）：勾了才把份數加回今日份數。
  const [resellable, setResellable] = useState<Record<number, boolean>>({});
  const returnLines = Object.entries(qtys)
    .filter(([, q]) => q > 0)
    .map(([id, q]) => ({ sale_line_id: Number(id), qty: q }))
    .sort((a, b) => a.sale_line_id - b.sale_line_id);
  const planKey = JSON.stringify(returnLines);
  const consentMatchesPlan = consentTaskId !== null && consentPlanKey === planKey;
  const paperRecalled = paperRecalledPlanKey === planKey;
  // **綁定本次退貨計畫**（與收回紙本／簽名同意／台灣Pay 退款一致）：店長是針對「退這些
  // 品項」去開紙本折讓單或作廢的。勾完再改品項卻仍算數，手上那張紙就與實退金額對不上
  // ——例如為 $500 部分退開的折讓單，被拿去放行 $1000 整筆退（Codex 對抗審查第十輪）。
  const manualPaperDisposed = manualPaperDisposedPlanKey === planKey;
  // 台灣Pay 的確認鍵**必須含實際退款腿金額**：同一組退貨品項，其台灣Pay 金額仍會因
  // 累計退款（別台終端先退過）或購物金優先分配而改變。只綁品項的話，店員退了 100 元、
  // 金額後來變成 200 元，勾勾仍有效 → 系統記成退 200（Codex 對抗審查第六輪 high）。
  // 注意：這只封住同一畫面內的變動；跨終端競態要後端簽發 plan token 才算真正解決。

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
  // 以**預覽回傳的權威旗標**為準；列表快取的 issue_channel 只作為尚未取得預覽時的提示。
  const isManualPaperSale =
    (previewData?.manual_paper_resolvable ?? false) ||
    (previewData === null && sale.invoice_issue_channel === "MANUAL_PAPER");
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
  // 退款去向的權威來源是後端預覽（與實際送出同一支函式；餐點只退外部付款這條規則只在後端）。
  // 預覽還沒回來時，二手退貨先以本機估算顯示；餐點的規則不在前端重算，先不顯示。
  const predictedRefund: RefundLeg[] =
    previewData !== null
      ? previewData.refund_tenders.map((leg) => ({
          tender_type: leg.tender_type as RefundTenderType,
          amount: parseNtd(leg.amount) ?? 0,
        }))
      : scope === "goods"
        ? refundPlan(tenders, previousRefund, refund)
        : [];
  const previewRefundSupported = previewData === null || previewData.refund_supported;
  // 含金額的確認鍵（見上方註解）：品項＋台灣Pay 腿金額都納入，任一改變即失效。
  const taiwanPayLegAmount =
    predictedRefund.find((leg) => leg.tender_type === "TAIWAN_PAY")?.amount ?? 0;
  const taiwanPayKey = `${planKey}|${taiwanPayLegAmount}`;
  const taiwanPayRefundConfirmed = taiwanPayConfirmedPlanKey === taiwanPayKey;
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
          // 純餐點退款：客人在顧客螢幕點「我同意」即可（docs/47 E3，後端會再驗全是餐點）。
          ...(scope === "food" ? { consent_mode: "TAP" as const } : {}),
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
    manualPaperDisposed,
    tapConsent: scope === "food",
  });

  const submit = useMutation({
    mutationFn: async () => {
      const invalid = validateReturnPlan(lines, qtys, reason, scope);
      if (invalid) throw new Error(invalid);
      // 持久化冪等鍵（Codex 第二輪 #3）：同銷售同退貨計畫恆得同鍵，跨對話框重掛/重整存活。
      const idemKey = getOrCreatePersistedIdemKey(
        idemScope,
        planFingerprintOf(qtys, reason),
      );
      const { data, error: apiError } = await api.POST("/api/v1/returns", {
        params: {
          header: { "Idempotency-Key": idemKey },
          // 手開紙本（docs/36）：店長確認已依國稅局程序處置紙本後，退貨只做本地反轉。
          query: isManualPaperSale ? { manual_paper_disposed: manualPaperDisposed } : {},
        },
        body: {
          sale_id: sale.id,
          reason: reason.trim(),
          lines: returnLines.map((line) =>
            resellable[line.sale_line_id] ? { ...line, resellable: true } : line,
          ),
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
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={scope === "food" ? "餐點退款" : "退貨"}
    >
      <div className="card pos-dialog" style={{ maxWidth: 560 }}>
        <h2>
          {scope === "food" ? "餐點退款" : "退貨"} #{sale.id}
        </h2>
        <p className="hint">
          {scope === "food"
            ? `${refundPolicy}這裡只能退餐點；同一張單的二手商品請到「交易紀錄」退貨。`
            : `${refundPolicy}庫存與會員點數會同步調整。餐點請到「餐飲交易紀錄」退款。`}
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
        {detail.isSuccess && (!refundSupported || !previewRefundSupported) && (
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
                {scope === "food" && <th>還能賣</th>}
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
                    {scope === "food" && (
                      <td>
                        <input
                          type="checkbox"
                          checked={resellable[line.id] ?? false}
                          aria-label={`${line.description} 這份還能賣`}
                          onChange={(e) =>
                            setResellable((prev) => ({ ...prev, [line.id]: e.target.checked }))
                          }
                        />
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
            </table>
          </>
        )}
        {detail.isSuccess && refundSupported && returnable.length === 0 && (
          <p className="hint">
            {scope === "food"
              ? "此單沒有可退款的餐點（可能已全部退過）。"
              : "此單沒有可退貨的商品（餐點請到餐飲交易紀錄退款）。"}
          </p>
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
        {/* 手開紙本（docs/36）：preview 未回或轉人工時，**不得**顯示任何外部退款指示
            ——店員會照做把錢退出去，送出才被擋（Codex 對抗審查第四輪 high）。 */}
        {isManualPaperSale && (
          <>
            <p role="alert" className="form-error">
              本筆為手開紙本發票，系統不代開折讓／作廢。請先依國稅局程序處置紙本
              （開立紙本折讓證明單或作廢並收回聯），完成前請勿退款給客人。
            </p>
            {canConfirmPaper ? (
              <label className="field field-toggle return-manual-paper-ack">
                <input
                  type="checkbox"
                  checked={manualPaperDisposed}
                  onChange={(e) =>
                    setManualPaperDisposedPlanKey(e.target.checked ? planKey : null)
                  }
                />
                <span className="field-label">
                  我已依國稅局程序處置本筆的紙本發票
                </span>
              </label>
            ) : (
              <p className="hint">本筆需店長確認紙本已處置後才能退貨。</p>
            )}
          </>
        )}
        {/* 台灣Pay 沒有退款 API，這個勾選等於「請店員先去 App 把錢退出去」。轉人工尚未解除
            （含手開紙本未經店長確認）時**不得**顯示，否則店員照做把錢退出去，送出才被擋。 */}
        {hasTaiwanPayRefund &&
          mayShowExternalRefundInstructions(previewData, {
            paperRecalled,
            consentTaskSigned: consentSigned,
            manualPaperDisposed,
          }) && (
          <label className="field field-toggle return-taiwan-confirm">
            <input
              type="checkbox"
              checked={taiwanPayRefundConfirmed}
              onChange={(event) =>
                setTaiwanPayConfirmedPlanKey(event.target.checked ? taiwanPayKey : null)
              }
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
                  <p className="form-success">
                    {scope === "food" ? "客人已同意" : "客人已簽名同意"}（簽署單號 #{consentTaskId}）
                  </p>
                ) : (
                  <>
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={pushConsent.isPending}
                      onClick={() => pushConsent.mutate()}
                    >
                      {pushConsent.isPending
                        ? "推送中…"
                        : scope === "food"
                          ? "請客人於顧客螢幕點選同意"
                          : "請客人於顧客螢幕簽名同意"}
                    </button>
                    {consentMatchesPlan && consentTask.data?.status === "PENDING" && (
                      <span className="hint">
                        {scope === "food" ? "已送出，等待客人同意…" : "已送出，等待客人簽名…"}
                      </span>
                    )}
                    {consentMatchesPlan && consentTask.data?.status === "SIGNING" && (
                      <span className="hint">
                        {scope === "food" ? "客人確認中…" : "客人簽名中…"}
                      </span>
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
              !previewRefundSupported ||
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
            {submit.isPending
              ? "處理中…"
              : `${scope === "food" ? "確認退款" : "確認退貨"} $${formatNtd(refund)}`}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
