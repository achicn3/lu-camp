// 型別化 API client（合約優先，docs/11）。型別來自後端 OpenAPI 生成的
// ./api-types（由 `pnpm gen:api` 產生；請勿手刻）。此為唯一一次手寫的薄封裝。
//
// 注意：api-types.ts 由生成管線產生；首次使用前需先跑 `pnpm gen:api`。
import createClient from "openapi-fetch";

import type { paths } from "./api-types";
import { UNAUTHORIZED_EVENT, clearToken, getToken } from "./token";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// fetch 延遲到呼叫時才解析全域（而非 createClient 時抓參考）：行為相同，
// 但測試以 stubGlobal 替換 fetch 才攔得到。
export const api = createClient<paths>({
  baseUrl: BASE_URL,
  fetch: (request) => globalThis.fetch(request),
});

// 帶 token；401（登入端點除外）→ 清 token 並廣播，讓 (authed) layout 導回登入。
api.use({
  onRequest({ request }) {
    const token = getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  onResponse({ request, response }) {
    if (response.status === 401 && !request.url.includes("/auth/login")) {
      clearToken();
      if (typeof window !== "undefined") {
        window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
      }
    }
    return response;
  },
});
