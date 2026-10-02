// 露坑線上點餐 Worker 進入點（docs/44）。路由：
//   客人（公開）：GET /api/menu、GET /api/status、GET /api/tables/:code、POST /api/orders、
//                GET /api/orders/:token、GET /photos/:hash.webp、GET /fonts/:hash.woff2
//   店內 backend（HMAC 簽章）：PUT /integration/menu、/integration/tables、/integration/photos/:hash、
//                              /integration/fonts/:hash
// 其餘交給靜態資產（點餐頁）。每個回應都加安全標頭。
import { verifyIntegration } from "./auth";
import { pullOrders, reportOrder, setStoreStatus } from "./integration-orders";
import { BodyTooLarge, error, readBody, withSecurityHeaders } from "./http";
import { type MediaKind, getMedia, mediaLimit, putMedia } from "./media";
import { MENU_MAX_BYTES, publishMenu, readMenu } from "./menu";
import { ORDER_MAX_BYTES, createOrder, readOrder, storeStatus } from "./orders";
import { publishTables, readTable } from "./tables";

const TABLES_MAX_BYTES = 64 * 1024;

const ROUTES: { method: string; pattern: RegExp; limit: number }[] = [
  { method: "PUT", pattern: /^\/integration\/menu$/, limit: MENU_MAX_BYTES },
  { method: "PUT", pattern: /^\/integration\/tables$/, limit: TABLES_MAX_BYTES },
  { method: "PUT", pattern: /^\/integration\/photos\/[^/]+$/, limit: mediaLimit("photos") },
  { method: "PUT", pattern: /^\/integration\/fonts\/[^/]+$/, limit: mediaLimit("fonts") },
  { method: "GET", pattern: /^\/integration\/orders$/, limit: 0 },
  { method: "POST", pattern: /^\/integration\/orders\/[^/]+\/status$/, limit: 1024 },
  { method: "PUT", pattern: /^\/integration\/store-status$/, limit: 1024 },
];

async function integrationRoute(req: Request, env: Env, storeId: number, path: string): Promise<Response> {
  const matched = ROUTES.filter((r) => r.pattern.test(path));
  if (matched.length === 0) return error("not_found", 404);
  const route = matched.find((r) => r.method === req.method);
  if (route === undefined) return error("method_not_allowed", 405);
  let body: Uint8Array;
  try {
    body = await readBody(req, route.limit);
  } catch (e) {
    if (e instanceof BodyTooLarge) return error("payload_too_large", 413);
    throw e;
  }
  if (!(await verifyIntegration(req, body, env, storeId))) return error("unauthorized", 401);
  const media = /^\/integration\/(photos|fonts)\/([^/]+)$/.exec(path);
  if (media) return putMedia(env, media[1] as MediaKind, media[2] ?? "", body);
  if (path === "/integration/menu") return publishMenu(env, storeId, body);
  if (path === "/integration/tables") return publishTables(env, storeId, body);
  if (path === "/integration/orders") return pullOrders(env, storeId);
  if (path === "/integration/store-status") return setStoreStatus(env, storeId, body);
  const status = /^\/integration\/orders\/([^/]+)\/status$/.exec(path);
  if (status) return reportOrder(env, storeId, status[1] ?? "", body);
  return error("not_found", 404);
}

async function route(req: Request, env: Env): Promise<Response> {
  const storeId = Number(env.STORE_ID);
  const path = new URL(req.url).pathname;
  if (path.startsWith("/integration/")) return integrationRoute(req, env, storeId, path);
  if (path === "/api/orders" && req.method === "POST") {
    try {
      return await createOrder(req, env, storeId, await readBody(req, ORDER_MAX_BYTES));
    } catch (e) {
      if (e instanceof BodyTooLarge) return error("payload_too_large", 413);
      throw e;
    }
  }
  if (req.method !== "GET" && req.method !== "HEAD") {
    return path.startsWith("/api/") ? error("method_not_allowed", 405) : env.ASSETS.fetch(req);
  }
  if (path === "/api/menu") return readMenu(env, storeId, req);
  if (path === "/api/status") return storeStatus(env, storeId);
  const orderView = /^\/api\/orders\/([^/]+)$/.exec(path);
  if (orderView) return readOrder(env, storeId, orderView[1] ?? "");
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
