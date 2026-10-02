// 桌位碼（docs/44 §4.1）：POS 發佈整份清單（取代舊的，重發＝舊碼失效）；客人用碼查桌名。
// 桌位碼不是身分證明，只決定桌號，不能用來查任何訂單。
import { error, json } from "./http";

export const TABLE_CODE = /^[A-Za-z0-9_-]{16,64}$/;
const MODES = new Set(["DINE_IN", "TAKEOUT"]);
const MAX_TABLES = 200;

type TableInput = { code: string; label: string; service_mode: string };

function validTables(v: unknown): v is { revision: number; tables: TableInput[] } {
  if (typeof v !== "object" || v === null) return false;
  const revision = (v as { revision?: unknown }).revision;
  if (typeof revision !== "number" || !Number.isSafeInteger(revision) || revision <= 0) return false;
  const tables = (v as { tables?: unknown }).tables;
  if (!Array.isArray(tables) || tables.length > MAX_TABLES) return false;
  const codes = new Set<string>();
  for (const t of tables) {
    if (typeof t !== "object" || t === null) return false;
    const { code, label, service_mode } = t as Record<string, unknown>;
    if (typeof code !== "string" || !TABLE_CODE.test(code) || codes.has(code)) return false;
    if (typeof label !== "string" || label.trim() === "" || label.length > 20) return false;
    if (typeof service_mode !== "string" || !MODES.has(service_mode)) return false;
    codes.add(code);
  }
  return true;
}

export async function publishTables(env: Env, storeId: number, raw: Uint8Array): Promise<Response> {
  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return error("invalid_json", 422);
  }
  if (!validTables(body)) return error("invalid_tables", 422);
  const rev = body.revision;
  // 版本檢查與整份取代在同一個交易（D1 batch）裡：先把版本往前推（只能往前），
  // 後面的刪除／新增都要求「目前版本就是我」才生效。晚到的舊推送版本較舊，什麼都不會改到。
  const current = "EXISTS (SELECT 1 FROM stores_meta WHERE store_id = ? AND tables_revision = ?)";
  const results = await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO stores_meta (store_id, tables_revision) VALUES (?, ?) " +
        "ON CONFLICT (store_id) DO UPDATE SET tables_revision = excluded.tables_revision " +
        "WHERE excluded.tables_revision > stores_meta.tables_revision",
    ).bind(storeId, rev),
    env.DB.prepare(`DELETE FROM tables WHERE store_id = ? AND ${current}`).bind(storeId, storeId, rev),
    ...body.tables.map((t) =>
      env.DB.prepare(
        `INSERT INTO tables (store_id, code, label, service_mode) SELECT ?, ?, ?, ? WHERE ${current}`,
      ).bind(storeId, t.code, t.label.trim(), t.service_mode, storeId, rev),
    ),
  ]);
  if (results[0]?.meta.changes === 0) {
    const meta = await env.DB.prepare("SELECT tables_revision FROM stores_meta WHERE store_id = ?")
      .bind(storeId)
      .first<{ tables_revision: number }>();
    // 同一版本重送（網路重試）照樣算成功；比目前舊＝晚到的舊推送，拒收。
    if (meta?.tables_revision !== rev) return error("stale_revision", 409);
  }
  return json({ count: body.tables.length, revision: rev });
}

export async function readTable(env: Env, storeId: number, code: string): Promise<Response> {
  if (!TABLE_CODE.test(code)) return error("not_found", 404);
  const row = await env.DB.prepare("SELECT label, service_mode FROM tables WHERE store_id = ? AND code = ?")
    .bind(storeId, code)
    .first<{ label: string; service_mode: string }>();
  return row === null ? error("not_found", 404) : json(row);
}
