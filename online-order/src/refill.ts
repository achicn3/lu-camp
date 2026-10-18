// 續點資格（店主 2026-10-19）：本桌 90 分鐘內點過、已付款的同一品項才能續點；外帶不續點；份數不限。
// 看的是「這一桌」不是「這支手機」：同桌換另一支手機掃碼也能續點。
// 店內 POS 用同一個時限做提醒（backend sales/service.py REFILL_LOOKBACK）。

export const REFILL_WINDOW_MS = 90 * 60 * 1000;

/** 本桌（內用）時限內已付款訂單點過的餐飲品項；外帶或沒有桌號回空集合。 */
export async function refillEligibleItems(
  env: Env, storeId: number, tableCode: string | null, serviceMode: string, now = Date.now(),
): Promise<Set<number>> {
  if (tableCode === null || serviceMode !== "DINE_IN") return new Set();
  const rows = await env.DB.prepare(
    "SELECT DISTINCT l.item_id FROM order_lines l JOIN orders o ON o.id = l.order_id " +
      "WHERE o.store_id = ? AND o.table_code = ? AND o.service_mode = 'DINE_IN' AND o.payment_status = 'PAID' " +
      "AND o.created_at >= ? AND l.item_id IS NOT NULL",
  ).bind(storeId, tableCode, now - REFILL_WINDOW_MS).all<{ item_id: number }>();
  return new Set(rows.results.map((row) => row.item_id));
}
