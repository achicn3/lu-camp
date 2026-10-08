// 客人送單與查單（docs/44 §4.2、§4.3、§4.5 C1、§7、§8；O4a 現金單）。
//
// 客人端的每個請求都當成可能是惡意的（§8）：先數速率、再驗格式、再看是否接單中、再驗 Turnstile，
// 價格一律依雲端目前生效的菜單重算；各項「未付款單上限」與建單在同一句 SQL 裡判斷（D1 依序寫入），
// 同時湧進來的請求也不會超過上限。
import { error, hex, json, sha256Hex } from "./http";
import { type OrderLineInput, priceOrder } from "./pricing";
import { linePayConfig } from "./linepay";
import { currentEffectiveMenu } from "./menu";
import { TABLE_CODE } from "./tables";

export const ORDER_MAX_BYTES = 16 * 1024;
const NOTE_MAX = 60;
const POS_STALE_MS = 2 * 60 * 1000;
const RATE_PER_MINUTE = 5;
const FLOOD_WINDOW_MS = 5 * 60 * 1000;
const FLOOD_LIMIT = 10;
const LIMITS = { device: 2, ip: 4, table: 4, storeCash: 15, storeLinePay: 15 } as const;
// 發票載具（docs/44 §4.4.2）：手機條碼 `/` 開頭共 8 碼；統編 8 位數字。
const MOBILE_CARRIER = /^\/[0-9A-Z.+-]{7}$/;
const TAX_ID = /^\d{8}$/;
const DEVICE_COOKIE = "lk_dev";
const DEVICE_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const IDEM_PATTERN = /^[A-Za-z0-9_-]{16,64}$/;
const TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

// 「還沒付、還在等」的單：限量品項被拒的不算（已經不會成立）。LINE Pay 付款中也還沒付。
export const OPEN_UNPAID =
  "payment_status IN ('UNPAID', 'PENDING', 'CONFIRMING') AND sync_status IN ('NEW', 'IMPORTED') " +
  "AND hold_status != 'REJECTED'";

interface OrderRequest {
  idempotency_key: string;
  table_code: string | null;
  payment_method: "CASH" | "LINE_PAY";
  invoice: { carrier: string | null; tax_id: string | null };
  turnstile_token: string;
  note: string;
  lines: OrderLineInput[];
}

/** 選填的發票資料：手機條碼或統編擇一；格式不對整張拒收。 */
function parseInvoice(value: unknown): OrderRequest["invoice"] | null {
  if (value === undefined || value === null) return { carrier: null, tax_id: null };
  if (typeof value !== "object" || Array.isArray(value)) return null;
  const { carrier = null, tax_id = null, ...rest } = value as Record<string, unknown>;
  if (Object.keys(rest).length > 0) return null;
  if (carrier !== null && (typeof carrier !== "string" || !MOBILE_CARRIER.test(carrier))) return null;
  if (tax_id !== null && (typeof tax_id !== "string" || !TAX_ID.test(tax_id))) return null;
  if (carrier !== null && tax_id !== null) return null;
  return { carrier: carrier as string | null, tax_id: tax_id as string | null };
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
  if (o.payment_method !== "CASH" && o.payment_method !== "LINE_PAY") return null;
  const invoice = parseInvoice(o.invoice);
  if (invoice === null) return null;
  if (typeof o.turnstile_token !== "string" || o.turnstile_token === "" || o.turnstile_token.length > 2048) {
    return null;
  }
  const note = o.note ?? "";
  if (typeof note !== "string" || [...note].length > NOTE_MAX) return null;
  if (!Array.isArray(o.lines)) return null;
  const lines: OrderLineInput[] = [];
  for (const l of o.lines) {
    if (typeof l !== "object" || l === null) return null;
    const { item_id, option_ids, qty, experience_id, catalog_product_id, ...rest } = l as Record<string, unknown>;
    if (catalog_product_id !== undefined) {
      // 帶回家商品：只有商品與數量（docs/63 §13）。
      if (typeof catalog_product_id !== "number" || typeof qty !== "number") return null;
      if (item_id !== undefined || option_ids !== undefined || experience_id !== undefined) return null;
      if (Object.keys(rest).length > 0) return null;
      lines.push({ catalog_product_id, qty });
      continue;
    }
    if (typeof item_id !== "number" || typeof qty !== "number" || !Array.isArray(option_ids)) return null;
    if (!option_ids.every((id) => typeof id === "number")) return null;
    if (experience_id !== undefined && experience_id !== null && typeof experience_id !== "number") return null;
    lines.push({
      item_id, option_ids: option_ids as number[], qty,
      ...(typeof experience_id === "number" ? { experience_id } : {}),
    });
  }
  return {
    idempotency_key: o.idempotency_key,
    table_code: o.table_code as string | null,
    payment_method: o.payment_method,
    invoice,
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


export function customerStatus(row: { payment_status: string; hold_status: string }): string {
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
      JSON.stringify([body.table_code, body.payment_method, body.note, body.lines, body.invoice]),
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

  if (body.payment_method === "LINE_PAY" && linePayConfig(env) === null) return error("linepay_unavailable", 422);
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
        "hold_status, note, created_at, updated_at, invoice_carrier, invoice_tax_id) " +
        "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'UNPAID', 'NEW', ?, ?, ?, ?, ?, ? WHERE " +
        `(SELECT count(*) FROM orders WHERE store_id = ? AND ${OPEN_UNPAID} AND device_id = ?) < ? AND ` +
        `(SELECT count(*) FROM orders WHERE store_id = ? AND ${OPEN_UNPAID} AND ip_hash = ?) < ? AND ` +
        `(? IS NULL OR (SELECT count(*) FROM orders WHERE store_id = ? AND ${OPEN_UNPAID} AND table_code = ?) < ?) AND ` +
        `(SELECT count(*) FROM orders WHERE store_id = ? AND ${OPEN_UNPAID} AND payment_method = ?) < ? ` +
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
      body.payment_method,
      holdStatus,
      body.note || null,
      now,
      now,
      body.invoice.carrier,
      body.invoice.tax_id,
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
      body.payment_method,
      body.payment_method === "CASH" ? LIMITS.storeCash : LIMITS.storeLinePay,
    ),
    ...priced.lines.map((l, i) =>
      env.DB.prepare(
        "INSERT INTO order_lines (order_id, store_id, line_no, item_id, catalog_product_id, name, option_ids, " +
          `unit_price, qty, line_total, limited, experience_id) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE ${exists}`,
      ).bind(
        orderId,
        storeId,
        i + 1,
        l.item_id ?? null,
        l.catalog_product_id ?? null,
        l.name,
        JSON.stringify(l.option_ids),
        l.unit_price,
        l.qty,
        l.line_total,
        l.limited ? 1 : 0,
        l.experience_id ?? null,
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
    "SELECT id, table_label, service_mode, total, payment_method, payment_status, hold_status, fulfillment, " +
      "linepay_result, note, created_at FROM orders WHERE store_id = ? AND token_hash = ?",
  )
    .bind(storeId, hash)
    .first<{
      id: string;
      table_label: string | null;
      service_mode: string;
      total: number;
      payment_status: string;
      hold_status: string;
      fulfillment: string;
      payment_method: string;
      linepay_result: string | null;
      note: string | null;
      created_at: number;
    }>();
  if (row === null) return error("not_found", 404);
  const lines = await env.DB.prepare(
    "SELECT name, qty, line_total, catalog_product_id IS NOT NULL AS take_home FROM order_lines " +
      "WHERE order_id = ? ORDER BY line_no",
  )
    .bind(row.id)
    .all<{ name: string; qty: number; line_total: number; take_home: number }>();
  return json(
    {
      status: customerStatus(row),
      table_label: row.table_label,
      service_mode: row.service_mode,
      total: row.total,
      note: row.note,
      created_at: new Date(row.created_at).toISOString(),
      // 帶回家商品要到櫃檯領：AWAITING＝付了錢還沒拿、HANDED_OVER＝已領取（docs/63 §13）。
      fulfillment: row.fulfillment,
      payment_method: row.payment_method,
      // LINE Pay 上一次沒付成的原因（CANCELLED／FAILED／EXPIRED）；客人可以重付。
      linepay_result: row.linepay_result,
      lines: lines.results.map(({ take_home, ...line }) => ({ ...line, take_home: take_home === 1 })),
    },
    200,
    { "Cache-Control": "no-store" },
  );
}

export async function storeStatus(env: Env, storeId: number): Promise<Response> {
  return json(
    {
      accepting: await accepting(env, storeId),
      turnstile_site_key: env.TURNSTILE_SITE_KEY ?? null,
      linepay: linePayConfig(env) !== null,
    },
    200,
    { "Cache-Control": "no-store" },
  );
}
