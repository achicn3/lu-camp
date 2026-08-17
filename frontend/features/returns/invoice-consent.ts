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
}

/**
 * 送出退貨前尚未滿足的條件；空陣列代表可送出。
 *
 * 預覽尚未回來（null）時不擋——否則畫面一載入就鎖死按鈕，店員無從得知原因。真正的
 * fail-closed 在後端：送出時會以當下狀態重新判斷一次並拒絕不合規的退貨。
 */
export function returnSubmitBlockers(
  preview: ReturnInvoicePreview | null,
  state: ReturnConsentState,
): string[] {
  if (preview === null) return [];
  const blockers: string[] = [];
  if (preview.invoice_action === "REVIEW_REQUIRED") {
    blockers.push("原發票狀態尚未確認，請待處理完成或聯繫管理者");
    return blockers;
  }
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
 * preview 還沒回來、或發票處置為 REVIEW_REQUIRED（手開紙本發票就是這一種）時一律不顯示：
 * 那些指示會讓店員先把錢退出去，送出退貨才被後端拒絕——錢收不回來、退貨也沒成立。
 * 送出本身另由 `returnSubmitBlockers` 擋，但**擋送出不夠**：指示先出現就已經害了。
 */
export function mayShowExternalRefundInstructions(
  preview: ReturnInvoicePreview | null,
): boolean {
  return preview !== null && preview.invoice_action !== "REVIEW_REQUIRED";
}
