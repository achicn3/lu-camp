// 寄售帳篷上官網（docs/97 §5）：店內 POS 推清單（簽章）；官網 Worker 經內部連線讀（點餐網址不提供）。
// 清單引用的照片必須先傳到 R2 tent-photos/；收下新清單後，沒被引用的帳篷照片（售出、下架）一律刪掉。
import { error, json, sha256Hex } from "./http";
import { HASH } from "./media";

export const TENTS_MAX_BYTES = 256 * 1024;
/** 官網 Worker 經 service binding 呼叫時用的主機名；外部網路的請求不會是這個主機名。 */
export const TENTS_HOST = "tents.internal";
const MAX_TENTS = 300;
const MAX_LIST = 20;
const PHOTO_PREFIX = "tent-photos/";
const SOLD_DAYS_DEFAULT = 30;
const STATUSES = new Set(["available", "reserved", "sold"]);
const YOUTUBE =
  /^https?:\/\/(?:www\.|m\.)?(?:youtu\.be\/|youtube(?:-nocookie)?\.com\/(?:watch\?(?:.*&)?v=|embed\/|shorts\/|live\/|v\/))[\w-]{11}(?:[?&#/].*)?$/;

interface Tent {
  id: string;
  photos: string[];
  [key: string]: unknown;
}

interface Feed {
  version: number;
  syncedAt: string;
  soldDays: number;
  tents: Tent[];
}

function str(v: unknown, max: number): boolean {
  return typeof v === "string" && v.length > 0 && v.length <= max;
}

function optStr(v: unknown, max: number): boolean {
  return v === null || str(v, max);
}

function strList(v: unknown, maxItems: number, maxLen: number): boolean {
  return Array.isArray(v) && v.length <= maxItems && v.every((s) => str(s, maxLen));
}

function nonNegInt(v: unknown): boolean {
  return typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
}

function validTent(t: unknown): t is Tent {
  if (typeof t !== "object" || t === null) return false;
  const o = t as Record<string, unknown>;
  return (
    str(o.id, 64) && optStr(o.brand, 100) && str(o.name, 200) && optStr(o.color, 30) &&
    (o.nights === null || nonNegInt(o.nights)) &&
    strList(o.missing, MAX_LIST, 100) && strList(o.defects, MAX_LIST, 100) && strList(o.extras, MAX_LIST, 100) &&
    Array.isArray(o.photos) && o.photos.length <= 8 && o.photos.every((h) => typeof h === "string" && HASH.test(h)) &&
    (o.video === null || (str(o.video, 300) && YOUTUBE.test(o.video as string))) &&
    (o.price === null || nonNegInt(o.price)) &&
    typeof o.status === "string" && STATUSES.has(o.status) &&
    str(o.listedAt, 40) && str(o.updatedAt, 40)
  );
}

function validFeed(f: unknown): f is Feed {
  if (typeof f !== "object" || f === null) return false;
  const o = f as Record<string, unknown>;
  return (
    nonNegInt(o.version) && (o.version as number) > 0 && str(o.syncedAt, 40) && nonNegInt(o.soldDays) &&
    Array.isArray(o.tents) && o.tents.length <= MAX_TENTS && o.tents.every(validTent)
  );
}

async function missingPhotos(env: Env, hashes: string[]): Promise<string[]> {
  const missing: string[] = [];
  for (const h of hashes) {
    if ((await env.MEDIA.head(`${PHOTO_PREFIX}${h}.webp`)) === null) missing.push(h);
  }
  return missing;
}

const LOCK_MS = 60_000;

/** 拿同店的處理鎖；別人持有且沒過期就拿不到（回 null）。D1 單一 UPSERT，判斷與寫入是同一步。 */
async function acquireLock(env: Env, storeId: number): Promise<string | null> {
  const token = crypto.randomUUID();
  const now = Date.now();
  const res = await env.DB.prepare(
    "INSERT INTO tent_lock (store_id, token, expires_at) VALUES (?, ?, ?) " +
      "ON CONFLICT (store_id) DO UPDATE SET token = excluded.token, expires_at = excluded.expires_at " +
      "WHERE tent_lock.expires_at < ?",
  )
    .bind(storeId, token, now + LOCK_MS, now)
    .run();
  return res.meta.changes === 1 ? token : null;
}

async function releaseLock(env: Env, storeId: number, token: string): Promise<void> {
  await env.DB.prepare("DELETE FROM tent_lock WHERE store_id = ? AND token = ?").bind(storeId, token).run();
}

async function holdsLock(env: Env, storeId: number, token: string): Promise<boolean> {
  const row = await env.DB.prepare("SELECT token, expires_at FROM tent_lock WHERE store_id = ?")
    .bind(storeId)
    .first<{ token: string; expires_at: number }>();
  return row !== null && row.token === token && row.expires_at > Date.now();
}

/**
 * 刪掉沒被清單引用的帳篷照片（售出、下架、還給寄售人的）。只動 tent-photos/。
 *
 * 呼叫端必須持有同店的處理鎖（publishTents），清單寫入與清照片不會交錯（Codex 對抗審查第一、三輪）。
 * 保留的是 D1 裡最新那份清單用到的照片；每刪一批前再確認鎖還是自己的——請求卡太久、鎖被別人接手時
 * 就停手（下一份清單會再清）。
 */
export async function sweepPhotos(env: Env, storeId: number, token: string): Promise<void> {
  const row = await env.DB.prepare("SELECT json FROM tent_listing WHERE store_id = ?")
    .bind(storeId)
    .first<{ json: string }>();
  const current = row === null ? [] : (JSON.parse(row.json) as Feed).tents.flatMap((t) => t.photos);
  const keep = new Set(current.map((h) => `${PHOTO_PREFIX}${h}.webp`));
  let cursor: string | undefined;
  do {
    const page = await env.MEDIA.list({ prefix: PHOTO_PREFIX, cursor });
    const stale = page.objects.map((o) => o.key).filter((k) => !keep.has(k));
    if (stale.length > 0) {
      if (!(await holdsLock(env, storeId, token))) return;
      await env.MEDIA.delete(stale);
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor !== undefined);
}

export async function publishTents(env: Env, storeId: number, raw: Uint8Array): Promise<Response> {
  let feed: unknown;
  try {
    feed = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return error("invalid_json", 422);
  }
  if (!validFeed(feed)) return error("invalid_feed", 422);
  const token = await acquireLock(env, storeId);
  if (token === null) return error("busy", 409);
  try {
    return await publishLocked(env, storeId, feed, token);
  } finally {
    await releaseLock(env, storeId, token);
  }
}

async function publishLocked(env: Env, storeId: number, feed: Feed, token: string): Promise<Response> {
  const text = JSON.stringify(feed);
  const digest = await sha256Hex(new TextEncoder().encode(text));
  const current = await env.DB.prepare("SELECT version, sha256 FROM tent_listing WHERE store_id = ?")
    .bind(storeId)
    .first<{ version: number; sha256: string }>();
  if (current !== null) {
    if (feed.version < current.version) return error("version_conflict", 409);
    if (feed.version === current.version) {
      return current.sha256 === digest ? json({ version: feed.version }) : error("version_conflict", 409);
    }
  }
  const hashes = [...new Set(feed.tents.flatMap((t) => t.photos))];
  const missing = await missingPhotos(env, hashes);
  if (missing.length > 0) return json({ error: "missing_photo", missing }, 409);
  await env.DB.prepare(
    "INSERT INTO tent_listing (store_id, version, json, sha256, synced_at) VALUES (?, ?, ?, ?, ?) " +
      "ON CONFLICT (store_id) DO UPDATE SET version = excluded.version, json = excluded.json, " +
      "sha256 = excluded.sha256, synced_at = excluded.synced_at WHERE excluded.version > tent_listing.version",
  )
    .bind(storeId, feed.version, text, digest, feed.syncedAt)
    .run();
  await sweepPhotos(env, storeId, token);
  return json({ version: feed.version });
}

const CACHE = { "Cache-Control": "public, max-age=60" };

export async function readTents(env: Env, storeId: number): Promise<Response> {
  const row = await env.DB.prepare("SELECT json FROM tent_listing WHERE store_id = ?")
    .bind(storeId)
    .first<{ json: string }>();
  if (row === null) {
    return json({ meta: { syncedAt: null, soldDays: SOLD_DAYS_DEFAULT }, tents: [] }, 200, CACHE);
  }
  const feed = JSON.parse(row.json) as Feed;
  // 相對網址：官網把 /tent-photos/* 轉到這裡，照片網址就掛在官網網域底下。
  const tents = feed.tents.map((t) => ({ ...t, photos: t.photos.map((h) => `/${PHOTO_PREFIX}${h}.webp`) }));
  return json({ meta: { syncedAt: feed.syncedAt, soldDays: feed.soldDays }, tents }, 200, CACHE);
}
