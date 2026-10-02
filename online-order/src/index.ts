// 露坑線上點餐 Worker 進入點（docs/44）。路由：
//   客人（公開）：GET /api/menu、GET /api/tables/:code、GET /photos/:hash.webp、GET /fonts/:hash.woff2
//   店內 backend（HMAC 簽章）：PUT /integration/menu、/integration/tables、/integration/photos/:hash、
//                              /integration/fonts/:hash
// 其餘交給靜態資產（點餐頁）。每個回應都加安全標頭。
import { verifyIntegration } from "./auth";
import { BodyTooLarge, error, readBody, withSecurityHeaders } from "./http";
import { type MediaKind, getMedia, mediaLimit, putMedia } from "./media";
import { MENU_MAX_BYTES, publishMenu, readMenu } from "./menu";
import { publishTables, readTable } from "./tables";

const TABLES_MAX_BYTES = 64 * 1024;

async function integrationRoute(req: Request, env: Env, storeId: number, path: string): Promise<Response> {
  if (req.method !== "PUT") return error("method_not_allowed", 405);
  const media = /^\/integration\/(photos|fonts)\/([^/]+)$/.exec(path);
  const limit = media
    ? mediaLimit(media[1] as MediaKind)
    : path === "/integration/menu"
      ? MENU_MAX_BYTES
      : TABLES_MAX_BYTES;
  let body: Uint8Array;
  try {
    body = await readBody(req, limit);
  } catch (e) {
    if (e instanceof BodyTooLarge) return error("payload_too_large", 413);
    throw e;
  }
  if (!(await verifyIntegration(req, body, env, storeId))) return error("unauthorized", 401);
  if (media) return putMedia(env, media[1] as MediaKind, media[2] ?? "", body);
  if (path === "/integration/menu") return publishMenu(env, storeId, body);
  if (path === "/integration/tables") return publishTables(env, storeId, body);
  return error("not_found", 404);
}

async function route(req: Request, env: Env): Promise<Response> {
  const storeId = Number(env.STORE_ID);
  const path = new URL(req.url).pathname;
  if (path.startsWith("/integration/")) return integrationRoute(req, env, storeId, path);
  if (req.method !== "GET" && req.method !== "HEAD") {
    return path.startsWith("/api/") ? error("method_not_allowed", 405) : env.ASSETS.fetch(req);
  }
  if (path === "/api/menu") return readMenu(env, storeId, req);
  const table = /^\/api\/tables\/([^/]+)$/.exec(path);
  if (table) return readTable(env, storeId, decodeURIComponent(table[1] ?? ""));
  if (path.startsWith("/api/")) return error("not_found", 404);
  const media = /^\/(photos|fonts)\/([^/]+)$/.exec(path);
  if (media) return getMedia(env, media[1] as MediaKind, media[2] ?? "");
  if (path.startsWith("/photos/") || path.startsWith("/fonts/")) return error("not_found", 404);
  return env.ASSETS.fetch(req);
}

export default {
  async fetch(req, env): Promise<Response> {
    return withSecurityHeaders(await route(req, env));
  },
} satisfies ExportedHandler<Env>;
