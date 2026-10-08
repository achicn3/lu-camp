// POS 可售狀態是目前菜單版本的完整覆蓋；名稱與價格仍只來自不可變的菜單快照。
import { error, json } from "./http";

export const AVAILABILITY_MAX_BYTES = 128 * 1024;

export interface AvailabilityEntry {
  id: number;
  available: boolean;
  remaining: number | null;
}

export interface AvailabilityUpdate {
  menu_version: number;
  revision: number;
  items: AvailabilityEntry[];
  options: AvailabilityEntry[];
  /** 帶回家商品的現量（docs/63 §13）；舊版店內程式不送。 */
  retail?: AvailabilityEntry[];
}

function keysAre(value: Record<string, unknown>, keys: string[], optional: string[] = []): boolean {
  return keys.every((key) => Object.hasOwn(value, key)) &&
    Object.keys(value).every((key) => keys.includes(key) || optional.includes(key));
}

function entriesAreValid(value: unknown): value is AvailabilityEntry[] {
  if (!Array.isArray(value)) return false;
  const seen = new Set<number>();
  for (const entry of value) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) return false;
    const row = entry as Record<string, unknown>;
    if (!keysAre(row, ["id", "available", "remaining"])) return false;
    if (!Number.isSafeInteger(row.id) || (row.id as number) <= 0 || seen.has(row.id as number)) return false;
    if (typeof row.available !== "boolean") return false;
    if (row.remaining !== null && (!Number.isSafeInteger(row.remaining) || (row.remaining as number) < 0)) {
      return false;
    }
    seen.add(row.id as number);
  }
  return true;
}

function validUpdate(value: unknown): value is AvailabilityUpdate {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return keysAre(row, ["menu_version", "revision", "items", "options"], ["retail"]) &&
    Number.isSafeInteger(row.menu_version) && (row.menu_version as number) > 0 &&
    Number.isSafeInteger(row.revision) && (row.revision as number) > 0 &&
    entriesAreValid(row.items) && entriesAreValid(row.options) &&
    (row.retail === undefined || entriesAreValid(row.retail));
}

export async function publishAvailability(env: Env, storeId: number, raw: Uint8Array): Promise<Response> {
  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return error("invalid_json", 422);
  }
  if (!validUpdate(body)) return error("invalid_availability", 422);
  // 排序、欄位順序正規化，讓相同 revision 的語意相同重送可安全回 200。
  const normalized: AvailabilityUpdate = {
    menu_version: body.menu_version,
    revision: body.revision,
    items: body.items.map(({ id, available, remaining }) => ({ id, available, remaining })).sort((a, b) => a.id - b.id),
    options: body.options.map(({ id, available, remaining }) => ({ id, available, remaining })).sort((a, b) => a.id - b.id),
    ...(body.retail === undefined ? {} : {
      retail: body.retail.map(({ id, available, remaining }) => ({ id, available, remaining })).sort((a, b) => a.id - b.id),
    }),
  };
  const payload = JSON.stringify(normalized);
  // 版本條件與 revision 比較在同一句寫入內，與菜單發佈的 stores_meta 更新原子排序。
  const result = await env.DB.prepare(
    "INSERT INTO menu_availability (store_id, menu_version, revision, json) " +
      "SELECT ?, ?, ?, ? WHERE EXISTS " +
      "(SELECT 1 FROM stores_meta WHERE store_id = ? AND menu_version = ?) " +
      "ON CONFLICT (store_id, menu_version) DO UPDATE SET revision = excluded.revision, json = excluded.json " +
      "WHERE excluded.revision > menu_availability.revision",
  ).bind(storeId, body.menu_version, body.revision, payload, storeId, body.menu_version).run();
  if (result.meta.changes === 0) {
    const current = await env.DB.prepare("SELECT menu_version FROM stores_meta WHERE store_id = ?")
      .bind(storeId).first<{ menu_version: number }>();
    if (current?.menu_version !== body.menu_version) return error("version_conflict", 409);
    const prior = await env.DB.prepare(
      "SELECT revision, json FROM menu_availability WHERE store_id = ? AND menu_version = ?",
    ).bind(storeId, body.menu_version).first<{ revision: number; json: string }>();
    if (prior === null || prior.revision < body.revision) return error("version_conflict", 409);
    if (prior.revision > body.revision) {
      return json({ error: "stale_revision", current_revision: prior.revision }, 409);
    }
    if (prior.json !== payload) {
      return json({ error: "revision_conflict", current_revision: prior.revision }, 409);
    }
  }
  return json({ menu_version: body.menu_version, revision: body.revision });
}
