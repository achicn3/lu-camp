// 顧客螢幕動畫的狀態（店主 2026-09-27 定稿規格 Z）。動畫只是 POS 狀態的視覺表現：
// 狀態由 POS 的購物車狀態決定，動畫跑到一半隨時可以被下一個 POS 事件打斷，
// 動畫本身失敗也不影響掃碼、金額、付款、發票。
export type LedgerAnimState =
  | "IDLE"
  | "TRANSITION_TO_CHECKOUT"
  | "CHECKOUT"
  | "ITEM_ADD"
  | "ITEM_UPDATE"
  | "ITEM_DELETE"
  | "PAYMENT_PENDING"
  | "PAYMENT_SUCCESS"
  | "PAYMENT_FAILED"
  | "CHECKOUT_CANCELLED"
  | "RETURN_TO_IDLE";

export type CartStatus = "DRAFT" | "FROZEN" | "PROCESSING" | "PAYMENT_UNCERTAIN" | "COMPLETED" | "CANCELLED" | "EXPIRED";

/** 筆這一刻在做的事（由寫字動畫回報；沒在寫就是 null）。 */
export type PenActivity = "ITEM_ADD" | "ITEM_UPDATE" | "ITEM_DELETE" | null;

/**
 * 結帳手帳這一刻的狀態。付款相關一律蓋過筆的動作（付款成功會立刻終止還在寫的商品動畫）。
 */
export function ledgerStateFor(input: {
  status: CartStatus;
  completed: boolean;
  paymentFailed: boolean;
  leaving: boolean;
  pen: PenActivity;
}): LedgerAnimState {
  if (input.leaving) return "CHECKOUT_CANCELLED";
  if (input.completed || input.status === "COMPLETED") return "PAYMENT_SUCCESS";
  if (input.status === "FROZEN" || input.status === "PROCESSING" || input.status === "PAYMENT_UNCERTAIN") return "PAYMENT_PENDING";
  if (input.paymentFailed) return "PAYMENT_FAILED";
  return input.pen ?? "CHECKOUT";
}
