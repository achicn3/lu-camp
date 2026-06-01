// 型別化 API client（合約優先，docs/11）。型別來自後端 OpenAPI 生成的
// ./api-types（由 `pnpm gen:api` 產生；請勿手刻）。此為唯一一次手寫的薄封裝。
//
// 注意：api-types.ts 由生成管線產生；首次使用前需先跑 `pnpm gen:api`。
import createClient from "openapi-fetch";

import type { paths } from "./api-types";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const api = createClient<paths>({ baseUrl: BASE_URL });
