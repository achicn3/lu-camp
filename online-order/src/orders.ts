// 客人送單與查單（docs/44 §4.2、§4.3、§4.5 C1、§7、§8；O4a 現金單）。
//
// 客人端的每個請求都當成可能是惡意的（§8）：先數速率、再驗格式、再看是否接單中、再驗 Turnstile，
// 價格一律依雲端目前生效的菜單重算；各項「未付款單上限」與建單在同一句 SQL 裡判斷（D1 依序寫入），
// 同時湧進來的請求也不會超過上限。
import { error, hex, json, sha256Hex } from "./http";
import { type OrderLineInput, priceOrder } from "./pricing";
import { currentEffectiveMenu } from "./menu";
import { TABLE_CODE } from "./tables";

export const ORDER_MAX_BYTES = 16 * 1024;
const NOTE_MAX = 60;
const POS_STALE_MS = 2 * 60 * 1000;
const RATE_PER_MINUTE = 5;
const FLOOD_WINDOW_MS = 5 * 60 * 1000;
const FLOOD_LIMIT = 10;
const LIMITS = { device: 2, ip: 4, table: 4, storeCash: 15 } as const;
const DEVICE_COOKIE = "lk_dev";
const DEVICE_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const IDEM_PATTERN = /^[A-Za-z0-9_-]{16,64}$/;
const TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

// 「還沒付、還在等」的單：限量品項被拒的不算（已經不會成立）。
const OPEN_UNPAID =
  "payment_status = 'UNPAID' AND sync_status IN ('NEW', 'IMPORTED') AND hold_status != 'REJECTED'";

interface OrderRequest {
  idempotency_key: string;
  table_code: string | null;
  payment_method: "CASH";
  turnstile_token: string;
  note: string;
  lines: OrderLineInput[];
}

function parseRequest(raw: Uint8Array): OrderRequest | null {
  let v: unknown;
  try {
    v = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return null;
  }
  if (typeof v !== "object" || v === null) return null;
  const o = v as Record<string, unknown>;
  if (typeof o.idempotency_key !== "string" || !IDEM_PATTERN.test(o.idempotency_key)) return null;
  if (o.table_code !== null && typeof o.table_code !== "string") return null;
  if (o.payment_method !== "CASH") return null; // LINE Pay 在 O5
  if (typeof o.turnstile_token !== "string" || o.turnstile_token === "" || o.turnstile_token.length > 2048) {
    return null;
  }
  const note = o.note ?? "";
  if (typeof note !== "string" || [...note].length > NOTE_MAX) return null;
  if (!Array.isArray(o.lines)) return null;
  const lines: OrderLineInput[] = [];
  for (const l of o.lines) {
    if (typeof l !== "object" || l === null) return null;
    const { item_id, option_ids, qty } = l as Record<string, unknown>;
    if (typeof item_id !== "number" || typeof qty !== "number" || !Array.isArray(option_ids)) return null;
    if (!option_ids.every((id) => typeof id === "number")) return null;
    lines.push({ item_id, option_ids: option_ids as number[], qty });
  }
  return {
    idempotency_key: o.idempotency_key,
    table_code: o.table_code as string | null,
    payment_method: "CASH",
    turnstile_token: o.turnstile_token,
    note: note.trim(),
    lines,
  };
}

function base64url(buf: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
}

async function hmac(secret: string, message: string): Promise<ArrayBuffer> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
}

/** 訂單權杖由密鑰＋冪等鍵算出：回應遺失後用同一個鍵重送，客人拿得回同一個權杖；D1 只存雜湊。 */
async function orderToken(env: Env, storeId: number, idemKey: string): Promise<string> {
  return base64url(await hmac(env.INTEGRATION_SECRET, `order-token:${storeId}:${idemKey}`));
}

function deviceFrom(req: Request): string | null {
  const cookie = req.headers.get("Cookie") ?? "";
  const match = new RegExp(`(?:^|;\\s*)${DEVICE_COOKIE}=([^;]+)`).exec(cookie);
  const value = match?.[1];
  return value !== undefined && DEVICE_PATTERN.test(value) ? value : null;
}

function deviceCookie(id: string): string {
  return `${DEVICE_COOKIE}=${id}; Path=/api; Max-Age=31536000; HttpOnly; Secure; SameSite=Strict`;
}

async function rateLimited(env: Env, storeId: number, ipHash: string, now: number): Promise<boolean> {
  const windowStart = Math.floor(now / 60000) * 60000;
  const row = await env.DB.prepare(
    "INSERT INTO rate_counters (store_id, key, window_start, count) VALUES (?, ?, ?, 1) " +
      "ON CONFLICT (store_id, key, window_start) DO UPDATE SET count = count + 1 RETURNING count",
  )
    .bind(storeId, `order:${ipHash}`, windowStart)
    .first<{ count: number }>();
  // 順手清掉舊的計數列，表不會一直長。
  await env.DB.prepare("DELETE FROM rate_counters WHERE store_id = ? AND window_start < ?")
    .bind(storeId, windowStart - 60000)
    .run();
  return (row?.count ?? 0) > RATE_PER_MINUTE;
}

export async function accepting(env: Env, storeId: number, now = Date.now()): Promise<boolean> {
  const meta = await env.DB.prepare(
    "SELECT accepting_orders, last_pos_seen_ms FROM stores_meta WHERE store_id = ?",
  )
    .bind(storeId)
    .first<{ accepting_orders: number; last_pos_seen_ms: number | null }>();
  if (meta === null || meta.accepting_orders !== 1 || meta.last_pos_seen_ms === null) return false;
  // POS 太久沒來拉單（店內斷線）＝自動視為暫停（§7）。
  return now - meta.last_pos_seen_ms <= POS_STALE_MS;
}

async function verifyTurnstile(env: Env, token: string, ip: string): Promise<boolean> {
  if (!env.TURNSTILE_SECRET) return false;
  const form = new FormData();
  form.append("secret", env.TURNSTILE_SECRET);
  form.append("response", token);
  if (ip) form.append("remoteip", ip);
  try {
    const resp = await fetch(TURNSTILE_URL, { method: "POST", body: form });
    const body = (await resp.json()) as { success?: boolean };
    return body.success === true;
  } catch {
    return false;
  }
}


function customerStatus(row: { payment_status: string; hold_status: string }): string {
  if (row.hold_status === "REJECTED") return "REJECTED";
  if (row.hold_status === "HOLD_REQUESTED") return "HOLD_REQUESTED";
  return row.payment_status;
}

async function existingOrder(
  env: Env,
  storeId: number,
  idemKey: string,
): Promise<{ fingerprint: string; payment_status: string; hold_status: string; total: number } | null> {
  return env.DB.prepare(
    "SELECT fingerprint, payment_status, hold_status, total FROM orders WHERE store_id = ? AND idem_key = ?",
  )
    .bind(storeId, idemKey)
    .first();
}

async function limitHit(
  env: Env,
  storeId: number,
  deviceId: string,
  ipHash: string,
  tableCode: string | null,
): Promise<string> {
  const count = async (where: string, ...binds: unknown[]): Promise<number> => {
    const row = await env.DB.prepare(`SELECT count(*) AS n FROM orders WHERE store_id = ? AND ${OPEN_UNPAID} AND ${where}`)
      .bind(storeId, ...binds)
      .first<{ n: number }>();
    return row?.n ?? 0;
  };
  if ((await count("device_id = ?", deviceId)) >= LIMITS.device) return "device_unpaid_limit";
  if ((await count("ip_hash = ?", ipHash)) >= LIMITS.ip) return "ip_unpaid_limit";
  if (tableCode !== null && (await count("table_code = ?", tableCode)) >= LIMITS.table) {
    return "table_unpaid_limit";
  }
  return "store_unpaid_limit";
}

export async function createOrder(req: Request, env: Env, storeId: number, raw: Uint8Array): Promise<Response> {
  const now = Date.now();
  const ip = req.headers.get("CF-Connecting-IP") ?? "";
  const ipHash = hex(await hmac(env.INTEGRATION_SECRET, `ip:${ip}`));
  if (await rateLimited(env, storeId, ipHash, now)) return error("rate_limited", 429);

  const body = parseRequest(raw);
  if (body === null) return error("invalid_request", 422);

  const fingerprint = await sha256Hex(
    new TextEncoder().encode(
      JSON.stringify([body.table_code, body.payment_method, body.note, body.lines]),
    ),
  );
  const token = await orderToken(env, storeId, body.idempotency_key);
  const prior = await existingOrder(env, storeId, body.idempotency_key);
  if (prior !== null) {
    // 冪等重送（C1）：同內容回同一張單；換了內容＝拿同一把鑰匙做別的事，拒收。
    if (prior.fingerprint !== fingerprint) return error("idempotency_conflict", 409);
    return json({ token, status: customerStatus(prior), total: prior.total });
  }
  // 暫停只擋新的單：已經成立的單（回應遺失後重送）要拿得回來，否則客人查不到自己的單（Codex O4 第一輪）。
  if (!(await accepting(env, storeId, now))) return error("not_accepting", 503);

  if (!(await verifyTurnstile(env, body.turnstile_token, ip))) return error("challenge_failed", 403);

  let tableLabel: string | null = null;
  let serviceMode = "TAKEOUT";
  if (body.table_code !== null) {
    if (!TABLE_CODE.test(body.table_code)) return error("table_not_found", 404);
    const table = await env.DB.prepare("SELECT label, service_mode FROM tables WHERE store_id = ? AND code = ?")
      .bind(storeId, body.table_code)
      .first<{ label: string; service_mode: string }>();
    if (table === null) return error("table_not_found", 404);
    tableLabel = table.label;
    serviceMode = table.service_mode;
  }

  const menu = await currentEffectiveMenu(env, storeId);
  if (menu === null) return error("menu_not_published", 503);
  const priced = priceOrder(menu, body.lines);
  if (!priced.ok) return json({ error: priced.reason, item_id: priced.item_id ?? null }, 422);

  const deviceId = deviceFrom(req) ?? base64url(crypto.getRandomValues(new Uint8Array(16)).buffer);
  const orderId = hex(crypto.getRandomValues(new Uint8Array(16)).buffer);
  const holdStatus = priced.needsHold ? "HOLD_REQUESTED" : "NONE";
  const exists = "EXISTS (SELECT 1 FROM orders WHERE id = ?)";
  const results = await env.DB.batch([
    // 建單與所有上限在同一句判斷（D1 依序寫入，不會兩張同時鑽過上限）。
    env.DB.prepare(
      "INSERT INTO orders (id, store_id, token_hash, idem_key, fingerprint, device_id, ip_hash, table_code, " +
        "table_label, service_mode, menu_version, total, payment_method, payment_status, sync_status, " +
        "hold_status, note, created_at, updated_at) " +
        "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CASH', 'UNPAID', 'NEW', ?, ?, ?, ? WHERE " +
        `(SELECT count(*) FROM orders WHERE store_id = ? AND ${OPEN_UNPAID} AND device_id = ?) < ? AND ` +
        `(SELECT count(*) FROM orders WHERE store_id = ? AND ${OPEN_UNPAID} AND ip_hash = ?) < ? AND ` +
        `(? IS NULL OR (SELECT count(*) FROM orders WHERE store_id = ? AND ${OPEN_UNPAID} AND table_code = ?) < ?) AND ` +
        `(SELECT count(*) FROM orders WHERE store_id = ? AND ${OPEN_UNPAID} AND payment_method = 'CASH') < ? ` +
        "ON CONFLICT (store_id, idem_key) DO NOTHING",
    ).bind(
      orderId,
      storeId,
      await sha256Hex(new TextEncoder().encode(token)),
      body.idempotency_key,
      fingerprint,
      deviceId,
      ipHash,
      body.table_code,
      tableLabel,
      serviceMode,
      menu.version,
      priced.total,
      holdStatus,
      body.note || null,
      now,
      now,
      storeId,
      deviceId,
      LIMITS.device,
      storeId,
      ipHash,
      LIMITS.ip,
      body.table_code,
      storeId,
      body.table_code,
      LIMITS.table,
      storeId,
      LIMITS.storeCash,
    ),
    ...priced.lines.map((l, i) =>
      env.DB.prepare(
        "INSERT INTO order_lines (order_id, store_id, line_no, item_id, name, option_ids, unit_price, qty, " +
          `line_total, limited) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE ${exists}`,
      ).bind(
        orderId,
        storeId,
        i + 1,
        l.item_id,
        l.name,
        JSON.stringify(l.option_ids),
        l.unit_price,
        l.qty,
        l.line_total,
        l.limited ? 1 : 0,
        orderId,
      ),
    ),
    env.DB.prepare(
      "INSERT INTO order_events (store_id, order_id, kind, to_state, source, at) " +
        `SELECT ?, ?, 'CREATED', ?, 'customer', ? WHERE ${exists}`,
    ).bind(storeId, orderId, holdStatus === "NONE" ? "UNPAID" : holdStatus, now, orderId),
  ]);

  if (results[0]?.meta.changes !== 1) {
    // 同一個冪等鍵剛好同時送兩次：另一個請求先建好了，照冪等回傳。
    const raced = await existingOrder(env, storeId, body.idempotency_key);
    if (raced !== null) {
      if (raced.fingerprint !== fingerprint) return error("idempotency_conflict", 409);
      return json({ token, status: customerStatus(raced), total: raced.total });
    }
    return error(await limitHit(env, storeId, deviceId, ipHash, body.table_code), 429);
  }

  await pauseOnFlood(env, storeId, now);
  const headers: Record<string, string> = {};
  if (deviceFrom(req) === null) headers["Set-Cookie"] = deviceCookie(deviceId);
  return json(
    {
      token,
      status: holdStatus === "NONE" ? "UNPAID" : holdStatus,
      total: priced.total,
      table_label: tableLabel,
      lines: priced.lines.map(({ name, qty, line_total }) => ({ name, qty, line_total })),
    },
    201,
    headers,
  );
}

/** 異常自動暫停（§8.3）：5 分鐘內新增未付款現金單超過門檻 → 雲端暫停接單，等店員在 POS 看過後恢復。 */
async function pauseOnFlood(env: Env, storeId: number, now: number): Promise<void> {
  const recent = await env.DB.prepare(
    `SELECT count(*) AS n FROM orders WHERE store_id = ? AND ${OPEN_UNPAID} AND payment_method = 'CASH' ` +
      "AND created_at >= ?",
  )
    .bind(storeId, now - FLOOD_WINDOW_MS)
    .first<{ n: number }>();
  if ((recent?.n ?? 0) > FLOOD_LIMIT) {
    await env.DB.prepare(
      "UPDATE stores_meta SET accepting_orders = 0, paused_reason = 'flood' WHERE store_id = ?",
    )
      .bind(storeId)
      .run();
  }
}

export async function readOrder(env: Env, storeId: number, token: string): Promise<Response> {
  if (!/^[A-Za-z0-9_-]{32,64}$/.test(token)) return error("not_found", 404);
  const hash = await sha256Hex(new TextEncoder().encode(token));
  const row = await env.DB.prepare(
    "SELECT id, table_label, service_mode, total, payment_status, hold_status, note, created_at " +
      "FROM orders WHERE store_id = ? AND token_hash = ?",
  )
    .bind(storeId, hash)
    .first<{
      id: string;
      table_label: string | null;
      service_mode: string;
      total: number;
      payment_status: string;
      hold_status: string;
      note: string | null;
      created_at: number;
    }>();
  if (row === null) return error("not_found", 404);
  const lines = await env.DB.prepare(
    "SELECT name, qty, line_total FROM order_lines WHERE order_id = ? ORDER BY line_no",
  )
    .bind(row.id)
    .all<{ name: string; qty: number; line_total: number }>();
  return json(
    {
      status: customerStatus(row),
      table_label: row.table_label,
      service_mode: row.service_mode,
      total: row.total,
      note: row.note,
      created_at: new Date(row.created_at).toISOString(),
      lines: lines.results,
    },
    200,
    { "Cache-Control": "no-store" },
  );
}

export async function storeStatus(env: Env, storeId: number): Promise<Response> {
  return json({ accepting: await accepting(env, storeId), turnstile_site_key: env.TURNSTILE_SITE_KEY ?? null }, 200, { "Cache-Control": "no-store" });
}
