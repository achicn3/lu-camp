// wrangler types 只會產生 wrangler.jsonc 裡的綁定；密鑰另外宣告（用 `wrangler secret put` 設定）。
interface Env {
  INTEGRATION_SECRET: string;
  TURNSTILE_SECRET: string;
}
