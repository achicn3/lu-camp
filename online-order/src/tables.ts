// 桌位碼（docs/44 §4.1）：POS 發佈整份清單（取代舊的，重發＝舊碼失效）；客人用碼查桌名。
// 桌位碼不是身分證明，只決定桌號，不能用來查任何訂單。
import { error, json } from "./http";

export const TABLE_CODE = /^[A-Za-z0-9_-]{16,64}$/;
const MODES = new Set(["DINE_IN", "TAKEOUT"]);
const MAX_TABLES = 200;

type TableInput = { code: string; label: string; service_mode: string };

function validTables(v: unknown): v is { tables: TableInput[] } {
  if (typeof v !== "object" || v === null) return false;
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
  await env.DB.batch([
    env.DB.prepare("DELETE FROM tables WHERE store_id = ?").bind(storeId),
    ...body.tables.map((t) =>
      env.DB.prepare("INSERT INTO tables (store_id, code, label, service_mode) VALUES (?, ?, ?, ?)").bind(
        storeId,
        t.code,
        t.label.trim(),
        t.service_mode,
      ),
    ),
  ]);
  return json({ count: body.tables.length });
}

export async function readTable(env: Env, storeId: number, code: string): Promise<Response> {
  if (!TABLE_CODE.test(code)) return error("not_found", 404);
  const row = await env.DB.prepare("SELECT label, service_mode FROM tables WHERE store_id = ? AND code = ?")
    .bind(storeId, code)
    .first<{ label: string; service_mode: string }>();
  return row === null ? error("not_found", 404) : json(row);
}
