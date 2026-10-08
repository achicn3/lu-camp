// wrangler types 只會產生 wrangler.jsonc 裡的綁定；密鑰另外宣告（用 `wrangler secret put` 設定）。
interface Env {
  INTEGRATION_SECRET: string;
  TURNSTILE_SECRET: string;
  TURNSTILE_SITE_KEY?: string;
  /** 線上 LINE Pay（O5a）；沒設就不提供 LINE Pay 付款。 */
  LINEPAY_CHANNEL_ID?: string;
  LINEPAY_CHANNEL_SECRET?: string;
  LINEPAY_API_BASE?: string;
}
