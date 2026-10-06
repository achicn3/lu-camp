import { applyD1Migrations } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { beforeEach } from "vitest";

await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

// 每項測試從乾淨的 D1／R2 開始（測試之間不共用狀態）。
beforeEach(async () => {
  await env.DB.batch(
    ["stores_meta", "menu_snapshots", "menu_availability", "tables", "integration_nonces", "orders", "order_lines", "order_events", "rate_counters"].map((t) =>
      env.DB.prepare(`DELETE FROM ${t}`),
    ),
  );
  const listed = await env.MEDIA.list();
  if (listed.objects.length > 0) await env.MEDIA.delete(listed.objects.map((o) => o.key));
});
