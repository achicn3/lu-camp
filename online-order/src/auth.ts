// 店內 backend → Worker 的請求簽章（docs/44 §5.2、§8.1 T7）。
//
// 簽章字串＝METHOD \n PATH(含 query) \n TIMESTAMP \n NONCE \n hex(SHA-256(body))，
// X-LuCamp-Signature = hex(HMAC-SHA256(INTEGRATION_SECRET, 簽章字串))。
// 時間差 ±5 分鐘；nonce 在 D1 記 10 分鐘，同一個 nonce 第二次就拒收（防重放）。
import { hex, sha256Hex } from "./http";

const MAX_SKEW_SECONDS = 300;
const NONCE_TTL_SECONDS = 600;
const NONCE_PATTERN = /^[A-Za-z0-9-]{16,64}$/;

async function hmacHex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message)));
}

/** 常數時間比較，避免從回應時間猜出簽章。 */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function verifyIntegration(
  req: Request,
  body: Uint8Array,
  env: Env,
  storeId: number,
  now = Math.floor(Date.now() / 1000),
): Promise<boolean> {
  const secret = env.INTEGRATION_SECRET;
  const timestamp = req.headers.get("X-LuCamp-Timestamp") ?? "";
  const nonce = req.headers.get("X-LuCamp-Nonce") ?? "";
  const signature = (req.headers.get("X-LuCamp-Signature") ?? "").toLowerCase();
  if (!secret || !/^\d{1,12}$/.test(timestamp) || !NONCE_PATTERN.test(nonce)) return false;
  if (Math.abs(now - Number(timestamp)) > MAX_SKEW_SECONDS) return false;
  const url = new URL(req.url);
  const canonical = [req.method, url.pathname + url.search, timestamp, nonce, await sha256Hex(body)].join(
    "\n",
  );
  if (!timingSafeEqual(await hmacHex(secret, canonical), signature)) return false;
  // 簽章對了才記 nonce（避免未授權的請求塞爆 nonce 表）；INSERT 撞到主鍵＝重放。
  await env.DB.prepare("DELETE FROM integration_nonces WHERE seen_at < ?")
    .bind(now - NONCE_TTL_SECONDS)
    .run();
  const inserted = await env.DB.prepare(
    "INSERT OR IGNORE INTO integration_nonces (store_id, nonce, seen_at) VALUES (?, ?, ?)",
  )
    .bind(storeId, nonce, now)
    .run();
  return inserted.meta.changes === 1;
}
