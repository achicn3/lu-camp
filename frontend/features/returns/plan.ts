// 退貨計畫（D-8 波次二，裁示 2026-07-16 解除 4B 擱置）：純函式供交易紀錄頁退貨對話框
// 與 vitest 直測。金額一律字串整數元（§6）。

import type { components } from "@/lib/api-types";

type SaleLine = components["schemas"]["SaleLineRead"];

/** 退貨對話框的範圍（docs/47）：交易紀錄退二手／一般商品，餐飲交易紀錄退餐點。 */
export type ReturnScope = "goods" | "food";

/** 交易紀錄頁可退的行別；餐點改在餐飲交易紀錄頁退款（docs/47）。 */
export const RETURNABLE_TYPES: ReadonlySet<string> = new Set([
  "CATALOG",
  "SERIALIZED",
  "BULK_LOT",
]);

export function isReturnable(line: SaleLine, scope: ReturnScope = "goods"): boolean {
  return scope === "food" ? line.line_type === "MENU" : RETURNABLE_TYPES.has(line.line_type);
}

/** 可退餘量＝購買數 − 已退數（部分退貨後單仍 COMPLETED，可再退剩餘；後端為最終防線）。 */
export function remainingQty(line: SaleLine): number {
  return Math.max(0, line.qty - (line.returned_qty ?? 0));
}

/** 退到第 x 件時客人累計應拿回的金額（與後端 refund_entitlement 同式）。 */
function refundEntitlement(line: SaleLine, returnedQty: number): number {
  const net = Number(line.net_amount);
  if (returnedQty >= line.qty) return net; // 全退恰好等於原實付，不經四捨五入
  return Math.round((net * returnedQty) / line.qty);
}

/** 預估退款額（差額法，與後端 refund_amount 同式）。
 *
 * 認**實付**（net_amount）不是單價：臨時折扣落在實付上，用單價會退多。
 * 差額法讓分次退貨的加總恰好等於原實付；每次各自四捨五入則會差幾元。
 * 這只是送出前的預估——實際金額以後端預覽／回應為準。
 */
export function computeRefund(lines: SaleLine[], qtys: Record<number, number>): number {
  let total = 0;
  for (const line of lines) {
    const qty = qtys[line.id] ?? 0;
    if (qty <= 0) continue;
    const already = line.returned_qty ?? 0;
    total += refundEntitlement(line, already + qty) - refundEntitlement(line, already);
  }
  return total;
}

/** 這張單先前已退的累計金額（退款渠道拆帳的基準）。 */
export function computePreviousRefund(lines: SaleLine[]): number {
  return lines.reduce(
    (sum, line) => sum + refundEntitlement(line, line.returned_qty ?? 0),
    0,
  );
}

/** 送出前防呆（後端仍是最終防線）：回錯誤訊息或 null。 */
export function validateReturnPlan(
  lines: SaleLine[],
  qtys: Record<number, number>,
  reason: string,
  scope: ReturnScope = "goods",
): string | null {
  if (reason.trim() === "") return "請填寫退貨原因";
  let any = false;
  for (const line of lines) {
    const qty = qtys[line.id] ?? 0;
    if (qty === 0) continue;
    if (!isReturnable(line, scope)) {
      return scope === "food"
        ? `「${line.description}」不是餐點，請到交易紀錄退貨`
        : `「${line.description}」是餐點，請到餐飲交易紀錄退款`;
    }
    if (qty < 0 || !Number.isInteger(qty)) return "退貨數量必須為正整數";
    const remaining = remainingQty(line);
    if (qty > remaining) {
      return `「${line.description}」退貨數量不可超過可退餘量 ${remaining}`;
    }
    any = true;
  }
  return any ? null : "請至少選擇一項退貨數量";
}
