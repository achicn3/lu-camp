// 桌位碼（docs/44 §4.1）：POS 發佈整份清單（取代舊的，重發＝舊碼失效）；客人用碼查桌名。
// 桌位碼不是身分證明，只決定桌號，不能用來查任何訂單。
import { error, json } from "./http";
import type { TableMinimum } from "./pricing";

export type { TableMinimum };

export const TABLE_CODE = /^[A-Za-z0-9_-]{16,64}$/;
const MODES = new Set(["DINE_IN", "TAKEOUT"]);
const MAX_TABLES = 200;

const MAX_MINIMUM_AMOUNT = 9_999_999;

type TableInput = { code: string; label: string; service_mode: string; minimum: TableMinimum | null };

function validMinimum(v: unknown, serviceMode: unknown): boolean {
  if (v === undefined || v === null) return true;
  if (serviceMode !== "DINE_IN" || typeof v !== "object" || Array.isArray(v)) return false;
  const { mode, amount, ...rest } = v as Record<string, unknown>;
  if (Object.keys(rest).length > 0) return false;
  if (mode === "per_person_item") return amount === undefined;
  return mode === "amount" && typeof amount === "number" && Number.isSafeInteger(amount)
    && amount > 0 && amount <= MAX_MINIMUM_AMOUNT;
}

/** D1 存的 JSON 字串 → 規則；壞資料視為不限制（不能讓客人卡住送不了單）。 */
export function parseMinimum(raw: string | null): TableMinimum | null {
  if (raw === null) return null;
  try {
    const v: unknown = JSON.parse(raw);
    return validMinimum(v, "DINE_IN") ? (v as TableMinimum | null) : null;
  } catch {
    return null;
  }
}

function validTables(v: unknown): v is { revision: number; tables: TableInput[] } {
  if (typeof v !== "object" || v === null) return false;
  const revision = (v as { revision?: unknown }).revision;
  if (typeof revision !== "number" || !Number.isSafeInteger(revision) || revision <= 0) return false;
  const tables = (v as { tables?: unknown }).tables;
  if (!Array.isArray(tables) || tables.length > MAX_TABLES) return false;
  const codes = new Set<string>();
  for (const t of tables) {
    if (typeof t !== "object" || t === null) return false;
    const { code, label, service_mode, minimum } = t as Record<string, unknown>;
    if (typeof code !== "string" || !TABLE_CODE.test(code) || codes.has(code)) return false;
    if (typeof label !== "string" || label.trim() === "" || label.length > 20) return false;
    if (typeof service_mode !== "string" || !MODES.has(service_mode)) return false;
    if (!validMinimum(minimum, service_mode)) return false;
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
        `INSERT INTO tables (store_id, code, label, service_mode, minimum) SELECT ?, ?, ?, ?, ? WHERE ${current}`,
      ).bind(storeId, t.code, t.label.trim(), t.service_mode, t.minimum ? JSON.stringify(t.minimum) : null, storeId, rev),
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
  const row = await env.DB.prepare("SELECT label, service_mode, minimum FROM tables WHERE store_id = ? AND code = ?")
    .bind(storeId, code)
    .first<{ label: string; service_mode: string; minimum: string | null }>();
  return row === null ? error("not_found", 404) : json({ ...row, minimum: parseMinimum(row.minimum) });
}
