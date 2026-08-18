// 退貨會如何處置原發票（後端 previewReturn 的結果）與送出前的把關條件。
// 判斷邏輯抽離為純函式：畫面只負責呈現，規則本身可被測試證偽。
import type { components } from "@/lib/api-types";

export type ReturnInvoicePreview = components["schemas"]["ReturnPreviewRead"];

const ACTION_LABELS: Record<string, string> = {
  VOID: "作廢原發票",
  ALLOWANCE: "開立折讓單",
  NONE: "不處理發票",
  REVIEW_REQUIRED: "需人工處理",
};

export function invoiceActionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

export interface ReturnConsentState {
  /** 店員已勾選「已向客人收回紙本發票證明聯」。 */
  paperRecalled: boolean;
  /** 客人已於顧客螢幕簽名同意（任務狀態為 SIGNED）。 */
  consentTaskSigned: boolean;
  /** 店長已確認依國稅局程序處置手開紙本發票（docs/36）。 */
  manualPaperDisposed?: boolean;
}

/**
 * 送出退貨前尚未滿足的條件；空陣列代表可送出。
 *
 * 預覽尚未回來（null）時不擋——否則畫面一載入就鎖死按鈕，店員無從得知原因。真正的
 * fail-closed 在後端：送出時會以當下狀態重新判斷一次並拒絕不合規的退貨。
 */
/**
 * 「轉人工」是否仍未解除。回傳阻擋原因，或 `null` 表示已解除。
 *
 * 兩種轉人工要分開（docs/36）：作廢在途那種真的不能動；手開紙本那種只要店長確認已依
 * 國稅局程序處置紙本就能繼續。不分開的話送出鍵永遠停用，退貨路徑形同不存在
 * ——後端能做、UI 走不到（Codex 對抗審查第九輪 high）。
 * **由後端的 `manual_paper_resolvable` 告知**，前端不自行從別的欄位推斷。
 */
function unresolvedReviewBlocker(
  preview: ReturnInvoicePreview,
  state: ReturnConsentState,
): string | null {
  if (preview.invoice_action !== "REVIEW_REQUIRED") return null;
  if (preview.manual_paper_resolvable && state.manualPaperDisposed === true) return null;
  return preview.manual_paper_resolvable
    ? "請先依國稅局程序處置紙本發票並由店長勾選確認"
    : "原發票狀態尚未確認，請待處理完成或聯繫管理者";
}

export function returnSubmitBlockers(
  preview: ReturnInvoicePreview | null,
  state: ReturnConsentState,
): string[] {
  if (preview === null) return [];
  const review = unresolvedReviewBlocker(preview, state);
  if (review !== null) return [review];
  const blockers: string[] = [];
  if (preview.requires_paper_recall && !state.paperRecalled) {
    blockers.push("請先向客人收回發票證明聯（紙本）並勾選確認");
  }
  if (preview.requires_customer_consent && !state.consentTaskSigned) {
    blockers.push("請先請客人於顧客螢幕簽名同意");
  }
  return blockers;
}

/**
 * 是否可以向店員顯示「請先去外部 App 退款」這類**不可逆**指示（docs/36）。
 *
 * preview 還沒回來、或**轉人工尚未解除**時一律不顯示：那些指示會讓店員先把錢退出去，
 * 送出退貨才被後端拒絕——錢收不回來、退貨也沒成立。擋送出不夠，指示先出現就已經害了。
 *
 * **不可改用「action 是否為 REVIEW_REQUIRED」當判準**：手開紙本經店長確認後 action 仍是
 * REVIEW_REQUIRED，那樣會讓手開紙本＋台灣Pay 的退貨永遠顯示不出確認框、退不了貨。
 *
 * 注意：收回紙本與客人簽名**刻意不納入**此判準——現行設計是三重確認同時出現
 * （見 `scripts/return-invoice-smoke.mjs` 情境 7）。改成序列化屬設計變更，待裁示。
 */
export function mayShowExternalRefundInstructions(
  preview: ReturnInvoicePreview | null,
  state: ReturnConsentState,
): boolean {
  return preview !== null && unresolvedReviewBlocker(preview, state) === null;
}
