declare namespace Cloudflare {
  interface Env {
    TEST_MIGRATIONS: import("cloudflare:test").D1Migration[];
    INTEGRATION_SECRET: string;
    TURNSTILE_SECRET: string;
  }
}
