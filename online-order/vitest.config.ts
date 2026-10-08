import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig(async () => {
  const migrations = await readD1Migrations("./migrations");
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.jsonc" },
        miniflare: {
          bindings: {
            TEST_MIGRATIONS: migrations,
            INTEGRATION_SECRET: "test-integration-secret",
            TURNSTILE_SECRET: "test-turnstile-secret",
            TURNSTILE_SITE_KEY: "test-public-site-key",
            LINEPAY_CHANNEL_ID: "1234567890",
            LINEPAY_CHANNEL_SECRET: "test-linepay-secret",
            LINEPAY_API_BASE: "https://linepay.test",
          },
        },
      }),
    ],
    test: { setupFiles: ["./test/apply-migrations.ts"] },
  };
});
