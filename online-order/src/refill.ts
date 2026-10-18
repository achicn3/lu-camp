// 續點資格（店主 2026-10-19）：本桌 90 分鐘內點過、已付款的同一品項才能續點；外帶不續點；份數不限。
// 看的是「這一桌」不是「這支手機」：同桌換另一支手機掃碼也能續點。
// 設定「不限品項續點」（菜單快照 refill_any_item）時：本桌 90 分鐘內有消費過（已付款）就能續點任何品項。
// 店內 POS 用同一個時限做提醒（backend sales/service.py REFILL_LOOKBACK）。

export const REFILL_WINDOW_MS = 90 * 60 * 1000;

export interface RefillEligibility {
  /** 本桌時限內點過的餐飲品項。 */
  items: Set<number>;
  /** 本桌時限內有沒有任何已付款的單（不限品項續點時看這個）。 */
  anySale: boolean;
}

/** 本桌（內用）時限內已付款訂單的續點資格；外帶或沒有桌號＝什麼都沒有。 */
export async function refillEligibility(
  env: Env, storeId: number, tableCode: string | null, serviceMode: string, now = Date.now(),
): Promise<RefillEligibility> {
  if (tableCode === null || serviceMode !== "DINE_IN") return { items: new Set(), anySale: false };
  const since = now - REFILL_WINDOW_MS;
  const [lines, orders] = await env.DB.batch([
    env.DB.prepare(
      "SELECT DISTINCT l.item_id FROM order_lines l JOIN orders o ON o.id = l.order_id " +
        "WHERE o.store_id = ? AND o.table_code = ? AND o.service_mode = 'DINE_IN' AND o.payment_status = 'PAID' " +
        "AND o.created_at >= ? AND l.item_id IS NOT NULL",
    ).bind(storeId, tableCode, since),
    env.DB.prepare(
      "SELECT COUNT(*) AS n FROM orders WHERE store_id = ? AND table_code = ? AND service_mode = 'DINE_IN' " +
        "AND payment_status = 'PAID' AND created_at >= ?",
    ).bind(storeId, tableCode, since),
  ]);
  const items = new Set((lines?.results as { item_id: number }[] | undefined ?? []).map((row) => row.item_id));
  const count = (orders?.results as { n: number }[] | undefined)?.[0]?.n ?? 0;
  return { items, anySale: count > 0 };
}

/** 這個品項在這一桌能不能續點。 */
export function refillAllowed(eligibility: RefillEligibility, itemId: number, anyItem: boolean): boolean {
  return anyItem ? eligibility.anySale : eligibility.items.has(itemId);
}
