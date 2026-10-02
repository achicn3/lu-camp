// 菜單快照（docs/44 §3.5）：店內 POS 發佈、客人讀目前生效的版本。
// 快照內容由店內 backend 組好（已驗證過的菜單），這裡只檢查形狀，擋住格式錯誤的發佈。
import { error, json, sha256Hex } from "./http";

export const MENU_MAX_BYTES = 512 * 1024;
const KEEP_VERSIONS = 5;

function isPositiveInt(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v) && v > 0;
}

function isWholeYuan(v: unknown): boolean {
  return typeof v === "number" && Number.isInteger(v) && v >= 0;
}

export function validSnapshot(s: unknown): s is { version: number; published_at: string } {
  if (typeof s !== "object" || s === null) return false;
  const o = s as Record<string, unknown>;
  if (!isPositiveInt(o.version) || typeof o.published_at !== "string") return false;
  if (!Array.isArray(o.categories) || !Array.isArray(o.items)) return false;
  return o.items.every((item: unknown) => {
    if (typeof item !== "object" || item === null) return false;
    const i = item as Record<string, unknown>;
    return isPositiveInt(i.id) && typeof i.name === "string" && isWholeYuan(i.unit_price);
  });
}

export async function publishMenu(env: Env, storeId: number, raw: Uint8Array): Promise<Response> {
  let snapshot: unknown;
  try {
    snapshot = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return error("invalid_json", 422);
  }
  if (!validSnapshot(snapshot)) return error("invalid_snapshot", 422);
  const text = JSON.stringify(snapshot);
  const digest = await sha256Hex(new TextEncoder().encode(text));
  // 先比目前生效的版本：比它舊的一律拒收（即使內容和當年一樣——發佈只能往前）。
  const meta = await env.DB.prepare("SELECT menu_version FROM stores_meta WHERE store_id = ?")
    .bind(storeId)
    .first<{ menu_version: number }>();
  if (meta !== null && snapshot.version < meta.menu_version) return error("version_conflict", 409);
  const existing = await env.DB.prepare(
    "SELECT sha256 FROM menu_snapshots WHERE store_id = ? AND version = ?",
  )
    .bind(storeId, snapshot.version)
    .first<{ sha256: string }>();
  if (existing !== null) {
    return existing.sha256 === digest ? json({ version: snapshot.version }) : error("version_conflict", 409);
  }
  await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO menu_snapshots (store_id, version, json, sha256, published_at) VALUES (?, ?, ?, ?, ?)",
    ).bind(storeId, snapshot.version, text, digest, snapshot.published_at),
    env.DB.prepare(
      "INSERT INTO stores_meta (store_id, menu_version) VALUES (?, ?) " +
        "ON CONFLICT (store_id) DO UPDATE SET menu_version = excluded.menu_version " +
        "WHERE excluded.menu_version > stores_meta.menu_version",
    ).bind(storeId, snapshot.version),
    env.DB.prepare(
      "DELETE FROM menu_snapshots WHERE store_id = ? AND version NOT IN " +
        "(SELECT version FROM menu_snapshots WHERE store_id = ? ORDER BY version DESC LIMIT ?)",
    ).bind(storeId, storeId, KEEP_VERSIONS),
  ]);
  return json({ version: snapshot.version });
}

export async function readMenu(env: Env, storeId: number, req: Request): Promise<Response> {
  const row = await env.DB.prepare(
    "SELECT s.version, s.json FROM stores_meta m JOIN menu_snapshots s " +
      "ON s.store_id = m.store_id AND s.version = m.menu_version WHERE m.store_id = ?",
  )
    .bind(storeId)
    .first<{ version: number; json: string }>();
  if (row === null) return error("menu_not_published", 404);
  const etag = `"menu-v${row.version}"`;
  const headers = { ETag: etag, "Cache-Control": "no-cache" };
  if (req.headers.get("If-None-Match") === etag) return new Response(null, { status: 304, headers });
  return new Response(row.json, {
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}
