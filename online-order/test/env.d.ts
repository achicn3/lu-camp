declare namespace Cloudflare {
  interface Env {
    TEST_MIGRATIONS: import("cloudflare:test").D1Migration[];
    INTEGRATION_SECRET: string;
    TURNSTILE_SECRET: string;
    LINEPAY_CHANNEL_ID?: string;
    LINEPAY_CHANNEL_SECRET?: string;
    LINEPAY_API_BASE?: string;
  }
}
