// 店內 POS ↔ 雲端的訂單同步（docs/44 §5.2、§4.6、§7；O4b）。呼叫端已驗過 HMAC。
//
// - 拉單（GET /integration/orders）：回還沒匯入的新單；同時是心跳（記下 POS 最後出現時間）。
// - 回報（POST /integration/orders/:id/status）：只接受合法的狀態轉換；重送同一結果冪等。
// - 暫停／恢復（PUT /integration/store-status）。
import { error, json } from "./http";

const PULL_LIMIT = 50;
const ORDER_ID = /^[0-9a-f]{32}$/;

const SYNC_TARGETS = new Set(["IMPORTED", "SETTLED", "VOIDED"]);
const HOLD_TARGETS = new Set(["HELD", "REJECTED", "NONE"]);
const PAYMENT_TARGETS = new Set(["PAID", "CANCELLED"]);
const FULFILLMENT_TARGETS = new Set(["AWAITING", "HANDED_OVER"]);

interface OrderRow {
  id: string;
  sync_status: string;
  hold_status: string;
  fulfillment: string;
  payment_status: string;
  row_version: number;
}

async function storeMeta(env: Env, storeId: number): Promise<{ accepting: boolean; paused_reason: string | null }> {
  const meta = await env.DB.prepare("SELECT accepting_orders, paused_reason FROM stores_meta WHERE store_id = ?")
    .bind(storeId)
    .first<{ accepting_orders: number; paused_reason: string | null }>();
  return { accepting: meta?.accepting_orders === 1, paused_reason: meta?.paused_reason ?? null };
}

export async function pullOrders(env: Env, storeId: number): Promise<Response> {
  const now = Date.now();
  await env.DB.prepare(
    "INSERT INTO stores_meta (store_id, last_pos_seen_ms) VALUES (?, ?) " +
      "ON CONFLICT (store_id) DO UPDATE SET last_pos_seen_ms = excluded.last_pos_seen_ms",
  )
    .bind(storeId, now)
    .run();
  const orders = await env.DB.prepare(
    "SELECT id, table_label, service_mode, menu_version, total, payment_method, payment_status, hold_status, " +
      "note, created_at FROM orders WHERE store_id = ? AND sync_status = 'NEW' ORDER BY created_at, id LIMIT ?",
  )
    .bind(storeId, PULL_LIMIT)
    .all<Record<string, unknown> & { id: string; created_at: number }>();
  const ids = orders.results.map((o) => o.id);
  const lines =
    ids.length === 0
      ? []
      : (
          await env.DB.prepare(
            `SELECT order_id, line_no, item_id, catalog_product_id, name, option_ids, unit_price, qty, line_total, limited, experience_id ` +
              `FROM order_lines WHERE order_id IN (${ids.map(() => "?").join(",")}) ORDER BY order_id, line_no`,
          )
            .bind(...ids)
            .all<{
              order_id: string;
              line_no: number;
              item_id: number | null;
              catalog_product_id: number | null;
              name: string;
              option_ids: string;
              unit_price: number;
              qty: number;
              line_total: number;
              limited: number;
              experience_id: number | null;
            }>()
        ).results;
  return json({
    ...(await storeMeta(env, storeId)),
    server_time: new Date(now).toISOString(),
    orders: orders.results.map((o) => ({
      ...o,
      created_at: new Date(o.created_at).toISOString(),
      lines: lines
        .filter((l) => l.order_id === o.id)
        .map(({ order_id: _, option_ids, limited, ...l }) => ({
          ...l,
          option_ids: JSON.parse(option_ids) as number[],
          limited: limited === 1,
        })),
    })),
  });
}

type Target = { sync_status?: string; hold_status?: string; payment_status?: string; fulfillment?: string };

function parseTarget(raw: Uint8Array): Target | null {
  let v: unknown;
  try {
    v = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return null;
  }
  if (typeof v !== "object" || v === null) return null;
  const { sync_status, hold_status, payment_status, fulfillment, ...rest } = v as Record<string, unknown>;
  if (Object.keys(rest).length > 0) return null;
  const t: Target = {};
  if (sync_status !== undefined) {
    if (typeof sync_status !== "string" || !SYNC_TARGETS.has(sync_status)) return null;
    t.sync_status = sync_status;
  }
  if (hold_status !== undefined) {
    if (typeof hold_status !== "string" || !HOLD_TARGETS.has(hold_status)) return null;
    t.hold_status = hold_status;
  }
  if (payment_status !== undefined) {
    if (typeof payment_status !== "string" || !PAYMENT_TARGETS.has(payment_status)) return null;
    t.payment_status = payment_status;
  }
  if (fulfillment !== undefined) {
    if (typeof fulfillment !== "string" || !FULFILLMENT_TARGETS.has(fulfillment)) return null;
    t.fulfillment = fulfillment;
  }
  if (Object.keys(t).length === 0) return null;
  // 付款結果必須和同步狀態一起報：收到錢＝銷售成立（SETTLED）；取消＝作廢（VOIDED）。
  if (t.payment_status === "PAID" && t.sync_status !== "SETTLED") return null;
  if (t.payment_status === "CANCELLED" && t.sync_status !== "VOIDED") return null;
  if ((t.sync_status === "SETTLED" || t.sync_status === "VOIDED") && t.payment_status === undefined) return null;
  return t;
}

const SYNC_FROM: Record<string, string[]> = {
  IMPORTED: ["NEW"],
  SETTLED: ["NEW", "IMPORTED"],
  VOIDED: ["NEW", "IMPORTED"],
};

/** 回傳 null＝可以轉；字串＝409 的原因。目前狀態已是目標＝冪等，視為可以。 */
function transitionError(row: OrderRow, t: Target): string | null {
  if (t.sync_status !== undefined && t.sync_status !== row.sync_status) {
    if (!SYNC_FROM[t.sync_status]?.includes(row.sync_status)) return "invalid_transition";
  }
  if (t.hold_status !== undefined && t.hold_status !== row.hold_status) {
    const reserving = row.hold_status === "HOLD_REQUESTED" && (t.hold_status === "HELD" || t.hold_status === "REJECTED");
    const expiring = row.hold_status === "HELD" && t.hold_status === "NONE" && row.payment_status === "UNPAID";
    if (!reserving && !expiring) return "invalid_transition";
  }
  if (t.payment_status !== undefined && t.payment_status !== row.payment_status) {
    if (row.payment_status !== "UNPAID") return "invalid_transition";
  }
  // 交貨（docs/63 §13）：成立銷售時標待交貨；店員交貨後才能到已領取，不能倒退。
  if (t.fulfillment !== undefined && t.fulfillment !== row.fulfillment) {
    const settled = (t.sync_status ?? row.sync_status) === "SETTLED";
    const awaiting = t.fulfillment === "AWAITING" && row.fulfillment === "NONE" && settled;
    const handed = t.fulfillment === "HANDED_OVER" && row.fulfillment === "AWAITING" && settled;
    if (!awaiting && !handed) return "invalid_transition";
  }
  return null;
}

export async function reportOrder(env: Env, storeId: number, id: string, raw: Uint8Array): Promise<Response> {
  if (!ORDER_ID.test(id)) return error("not_found", 404);
  const target = parseTarget(raw);
  if (target === null) return error("invalid_status", 422);
  const row = await env.DB.prepare(
    "SELECT id, sync_status, hold_status, payment_status, fulfillment, row_version FROM orders " +
      "WHERE store_id = ? AND id = ?",
  )
    .bind(storeId, id)
    .first<OrderRow>();
  if (row === null) return error("not_found", 404);
  const problem = transitionError(row, target);
  if (problem !== null) return error(problem, 409);

  const next = {
    sync_status: target.sync_status ?? row.sync_status,
    hold_status: target.hold_status ?? row.hold_status,
    payment_status: target.payment_status ?? row.payment_status,
    fulfillment: target.fulfillment ?? row.fulfillment,
  };
  const changed = (Object.keys(next) as (keyof typeof next)[]).filter((k) => next[k] !== row[k]);
  if (changed.length === 0) return json({ id, ...next });
  const now = Date.now();
  const results = await env.DB.batch([
    // 樂觀鎖：拿到的版本若已被別的回報改過，這次不寫（下面回 409，POS 會重拉重報）。
    env.DB.prepare(
      "UPDATE orders SET sync_status = ?, hold_status = ?, payment_status = ?, fulfillment = ?, updated_at = ?, " +
        "row_version = row_version + 1 WHERE store_id = ? AND id = ? AND row_version = ?",
    ).bind(next.sync_status, next.hold_status, next.payment_status, next.fulfillment, now, storeId, id, row.row_version),
    ...changed.map((k) =>
      env.DB.prepare(
        "INSERT INTO order_events (store_id, order_id, kind, from_state, to_state, source, at) " +
          "SELECT ?, ?, ?, ?, ?, 'pos', ? WHERE changes() = 1",
      ).bind(storeId, id, next[k], row[k], next[k], now),
    ),
  ]);
  if (results[0]?.meta.changes !== 1) return error("conflict_retry", 409);
  return json({ id, ...next });
}

export async function setStoreStatus(env: Env, storeId: number, raw: Uint8Array): Promise<Response> {
  let v: unknown;
  try {
    v = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return error("invalid_json", 422);
  }
  const accepting = (v as { accepting?: unknown } | null)?.accepting;
  if (typeof accepting !== "boolean") return error("invalid_status", 422);
  await env.DB.prepare(
    "INSERT INTO stores_meta (store_id, accepting_orders, paused_reason) VALUES (?, ?, NULL) " +
      "ON CONFLICT (store_id) DO UPDATE SET accepting_orders = excluded.accepting_orders, " +
      // 恢復接單＝店員看過了，清掉自動暫停的原因；手動暫停不記原因。
      "paused_reason = NULL",
  )
    .bind(storeId, accepting ? 1 : 0)
    .run();
  return json(await storeMeta(env, storeId));
}
