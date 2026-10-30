// 露坑線上點餐 Worker 進入點（docs/44）。路由：
//   客人（公開）：GET /api/menu、GET /api/status、GET /api/tables/:code、POST /api/orders、
//                GET /api/orders/:token、POST /api/orders/:token/linepay(/confirm|/cancel)、
//                GET /photos/:hash.webp、GET /fonts/:hash.woff2
//   官網（docs/97）：只透過官網 Worker 的內部連線（service binding，主機名 tents.internal）——
//                GET /api/tents、GET /tent-photos/:hash.webp；點餐網址上這兩條一律 404。
//   店內 backend（HMAC 簽章）：PUT /integration/menu、/integration/menu/availability、/integration/tables、/integration/photos/:hash、
//                              /integration/fonts/:hash、/integration/tents、/integration/tent-photos/:hash
// 其餘交給靜態資產（點餐頁）。每個回應都加安全標頭。
import { verifyIntegration } from "./auth";
import { AVAILABILITY_MAX_BYTES, publishAvailability } from "./availability";
import { pullOrders, reportOrder, setLinePayEnabled, setStoreStatus } from "./integration-orders";
import { BodyTooLarge, error, readBody, withSecurityHeaders } from "./http";
import { type MediaKind, getMedia, mediaLimit, putMedia } from "./media";
import { cancelLinePay, confirmLinePay, startLinePay } from "./linepay-orders";
import { MENU_MAX_BYTES, publishMenu, readMenu } from "./menu";
import { ORDER_MAX_BYTES, createOrder, paymentRateLimited, readOrder, storeStatus } from "./orders";
import { publishTables, readTable } from "./tables";
import { TENTS_HOST, TENTS_MAX_BYTES, publishTents, readTents } from "./tents";

const TABLES_MAX_BYTES = 64 * 1024;

const ROUTES: { method: string; pattern: RegExp; limit: number }[] = [
  { method: "PUT", pattern: /^\/integration\/menu$/, limit: MENU_MAX_BYTES },
  { method: "PUT", pattern: /^\/integration\/menu\/availability$/, limit: AVAILABILITY_MAX_BYTES },
  { method: "PUT", pattern: /^\/integration\/tables$/, limit: TABLES_MAX_BYTES },
  { method: "PUT", pattern: /^\/integration\/photos\/[^/]+$/, limit: mediaLimit("photos") },
  { method: "PUT", pattern: /^\/integration\/fonts\/[^/]+$/, limit: mediaLimit("fonts") },
  { method: "PUT", pattern: /^\/integration\/tent-photos\/[^/]+$/, limit: mediaLimit("tent-photos") },
  { method: "PUT", pattern: /^\/integration\/tents$/, limit: TENTS_MAX_BYTES },
  { method: "GET", pattern: /^\/integration\/orders$/, limit: 0 },
  { method: "POST", pattern: /^\/integration\/orders\/[^/]+\/status$/, limit: 1024 },
  { method: "PUT", pattern: /^\/integration\/store-status$/, limit: 1024 },
  { method: "PUT", pattern: /^\/integration\/linepay$/, limit: 1024 },
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
  const media = /^\/integration\/(photos|fonts|tent-photos)\/([^/]+)$/.exec(path);
  if (media) return putMedia(env, media[1] as MediaKind, media[2] ?? "", body);
  if (path === "/integration/menu") return publishMenu(env, storeId, body);
  if (path === "/integration/menu/availability") return publishAvailability(env, storeId, body);
  if (path === "/integration/tables") return publishTables(env, storeId, body);
  if (path === "/integration/tents") return publishTents(env, storeId, body);
  if (path === "/integration/orders") return pullOrders(env, storeId);
  if (path === "/integration/store-status") return setStoreStatus(env, storeId, body);
  if (path === "/integration/linepay") return setLinePayEnabled(env, storeId, body);
  const status = /^\/integration\/orders\/([^/]+)\/status$/.exec(path);
  if (status) return reportOrder(env, storeId, status[1] ?? "", body);
  return error("not_found", 404);
}

/** 官網 Worker 經內部連線來拿寄售帳篷清單與照片（docs/97 §5）；其他路徑一律沒有。 */
function tentsRoute(req: Request, env: Env, storeId: number, path: string): Promise<Response> | Response {
  if (req.method !== "GET" && req.method !== "HEAD") return error("method_not_allowed", 405);
  if (path === "/api/tents") return readTents(env, storeId);
  const photo = /^\/tent-photos\/([^/]+)$/.exec(path);
  if (photo) return getMedia(env, "tent-photos", photo[1] ?? "");
  return error("not_found", 404);
}

async function route(req: Request, env: Env): Promise<Response> {
  const storeId = Number(env.STORE_ID);
  const path = new URL(req.url).pathname;
  if (path.startsWith("/integration/")) return integrationRoute(req, env, storeId, path);
  if (new URL(req.url).hostname === TENTS_HOST) return tentsRoute(req, env, storeId, path);
  if (path === "/api/orders" && req.method === "POST") {
    try {
      return await createOrder(req, env, storeId, await readBody(req, ORDER_MAX_BYTES));
    } catch (e) {
      if (e instanceof BodyTooLarge) return error("payload_too_large", 413);
      throw e;
    }
  }
  const pay = /^\/api\/orders\/([^/]+)\/linepay(\/confirm|\/cancel)?$/.exec(path);
  if (pay && req.method === "POST") {
    // 維持既有空 body 相容性；任何付款副作用前先限制實際讀取量。
    let raw: Uint8Array;
    try {
      raw = await readBody(req, ORDER_MAX_BYTES);
    } catch (e) {
      if (e instanceof BodyTooLarge) return error("payload_too_large", 413);
      throw e;
    }
    const token = pay[1] ?? "";
    if (pay[2] === "/confirm") return confirmLinePay(env, storeId, token);
    if (pay[2] === "/cancel") return cancelLinePay(env, storeId, token);
    if (await paymentRateLimited(req, env, storeId)) return error("rate_limited", 429);
    return startLinePay(req, env, storeId, token, raw);
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
  if (path.startsWith("/photos/") || path.startsWith("/fonts/") || path.startsWith("/tent-photos/")) {
    return error("not_found", 404);
  }
  return env.ASSETS.fetch(req);
}

export default {
  async fetch(req, env): Promise<Response> {
    return withSecurityHeaders(await route(req, env));
  },
} satisfies ExportedHandler<Env>;
