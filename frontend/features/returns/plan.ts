// 退貨計畫（D-8 波次二，裁示 2026-07-16 解除 4B 擱置）：純函式供交易紀錄頁退貨對話框
// 與 vitest 直測。金額一律字串整數元（§6）。

import type { components } from "@/lib/api-types";

type SaleLine = components["schemas"]["SaleLineRead"];

/** v1 後端支援退貨的行別（餐飲現做即售不退）。 */
export const RETURNABLE_TYPES: ReadonlySet<string> = new Set([
  "CATALOG",
  "SERIALIZED",
  "BULK_LOT",
]);

export function isReturnable(line: SaleLine): boolean {
  return RETURNABLE_TYPES.has(line.line_type);
}

/** 預估退款額 = Σ 折後單價 × 退貨數（與後端 refund_amount 同式）。 */
export function computeRefund(lines: SaleLine[], qtys: Record<number, number>): number {
  let total = 0;
  for (const line of lines) {
    const qty = qtys[line.id] ?? 0;
    if (qty > 0) total += Number(line.unit_price) * qty;
  }
  return total;
}

/** 送出前防呆（後端仍是最終防線）：回錯誤訊息或 null。 */
export function validateReturnPlan(
  lines: SaleLine[],
  qtys: Record<number, number>,
  reason: string,
): string | null {
  if (reason.trim() === "") return "請填寫退貨原因";
  let any = false;
  for (const line of lines) {
    const qty = qtys[line.id] ?? 0;
    if (qty === 0) continue;
    if (!isReturnable(line)) return `「${line.description}」為餐飲品項，不支援退貨`;
    if (qty < 0 || !Number.isInteger(qty)) return "退貨數量必須為正整數";
    if (qty > line.qty) return `「${line.description}」退貨數量不可超過購買數 ${line.qty}`;
    any = true;
  }
  return any ? null : "請至少選擇一項退貨數量";
}
