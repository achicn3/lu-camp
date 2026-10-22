// 續點不能用 LINE Pay、要到櫃檯結帳（店主 2026-10-22）真瀏覽器煙霧：本機 Worker/D1＋真客人頁。
// 本桌先點過、付過手沖 → 另一支手機加一杯續點：付款方式的 LINE Pay 反灰、寫「續點請到櫃檯結帳」，
// 只能櫃檯結帳；沒有續點的單 LINE Pay 照常能選。
// 只對隔離的本機 wrangler dev（Cloudflare 官方 Turnstile 測試金鑰）跑，絕不指向正式站。
import assert from "node:assert/strict";
import { createHash, createHmac, randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium, devices } from "../../frontend/node_modules/playwright/index.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "Set SMOKE_ALLOW_WRITE=1 for the isolated local Worker");
const base = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8787";
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/, "只對本機 Worker 跑");
const secret = process.env.SMOKE_SECRET;
assert.ok(secret, "Set SMOKE_SECRET to the local Worker's INTEGRATION_SECRET");
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "online-refill-linepay");
mkdirSync(shots, { recursive: true });

async function integration(method, path, value) {
  const body = value === undefined ? "" : JSON.stringify(value);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = randomUUID();
  const hash = createHash("sha256").update(body).digest("hex");
  const signature = createHmac("sha256", secret).update([method, path, timestamp, nonce, hash].join("\n")).digest("hex");
  const response = await fetch(`${base}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json", "X-LuCamp-Timestamp": timestamp,
      "X-LuCamp-Nonce": nonce, "X-LuCamp-Signature": signature,
    },
    body: method === "GET" ? undefined : body,
  });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${await response.text()}`);
  return response.json();
}

const run = Date.now();
const coffee = `手沖 ${run}`;
const T1 = `smokeLP${run}`.padEnd(16, "x");
await integration("PUT", "/integration/menu", {
  version: run, published_at: new Date().toISOString(), store_name: "露坑", font: null, refill_pct: 80,
  categories: [{ id: 1, name: "咖啡" }],
  items: [{ id: 5, name: coffee, description: null, category_id: 1, unit_price: 200, photo: null,
    available: true, remaining: null, refillable: true, option_groups: [] }],
});
await integration("PUT", "/integration/tables", { revision: run, tables: [{ code: T1, label: "T1", service_mode: "DINE_IN" }] });
await integration("PUT", "/integration/store-status", { accepting: true });
await integration("PUT", "/integration/linepay", { enabled: true });
await integration("GET", "/integration/orders");

const browser = await chromium.launch();
const errors = [];
async function phone() {
  const context = await browser.newContext({ ...devices["iPhone 13"] });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(String(error)));
  return page;
}
const linePayRadio = (page) => page.locator('input[name="pay-method"][value="LINE_PAY"]');
try {
  // 手機 A：T1 點一杯手沖、櫃檯結帳，POS 收完錢回報已付款
  const a = await phone();
  await a.goto(`${base}/t/${T1}`, { waitUntil: "networkidle" });
  await a.getByRole("button", { name: "咖啡", exact: true }).click();
  await a.locator(".item", { hasText: coffee }).locator(".item-detail").click();
  await a.getByRole("button", { name: "加入購物車" }).click();
  await a.getByRole("button", { name: /購物車 1 份/ }).click();
  assert.equal(await linePayRadio(a).isDisabled(), false, "一般點：LINE Pay 可選");
  await a.getByRole("button", { name: "送出訂單" }).click();
  await a.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 30000 });
  const pulled = await integration("GET", "/integration/orders");
  const first = pulled.orders.find((o) => o.lines.some((l) => l.name === coffee));
  assert.ok(first, "POS 拉到手機 A 的單");
  await integration("POST", `/integration/orders/${first.id}/status`, { sync_status: "IMPORTED" });
  await integration("POST", `/integration/orders/${first.id}/status`, { sync_status: "SETTLED", payment_status: "PAID" });

  // 手機 B：同桌加一杯續點 → LINE Pay 反灰、提示到櫃檯結帳
  const b = await phone();
  await b.goto(`${base}/t/${T1}`, { waitUntil: "networkidle" });
  await b.getByRole("button", { name: "咖啡", exact: true }).click();
  await b.locator(".item", { hasText: coffee }).locator(".item-detail").click();
  await b.getByLabel("我是續點（8 折，加料也打折）").check();
  await b.getByRole("button", { name: "加入購物車" }).click();
  await b.getByRole("button", { name: /購物車 1 份/ }).click();
  await b.getByText("續點請到櫃檯結帳").waitFor();
  assert.equal(await linePayRadio(b).isDisabled(), true, "有續點：LINE Pay 不能選");
  assert.equal(await b.locator('input[name="pay-method"][value="CASH"]').isChecked(), true, "只能櫃檯結帳");
  await b.getByRole("button", { name: "送出訂單" }).scrollIntoViewIfNeeded();
  await b.screenshot({ path: join(shots, "1-refill-counter-only.png") });

  // 同桌另一支手機一般點（沒有續點）：LINE Pay 照常能選
  const c = await phone();
  await c.goto(`${base}/t/${T1}`, { waitUntil: "networkidle" });
  await c.getByRole("button", { name: "咖啡", exact: true }).click();
  await c.locator(".item", { hasText: coffee }).locator(".item-detail").click();
  await c.getByRole("button", { name: "加入購物車" }).click();
  await c.getByRole("button", { name: /購物車 1 份/ }).click();
  assert.equal(await linePayRadio(c).isDisabled(), false, "沒續點：LINE Pay 可選");
  assert.equal(await c.getByText("續點請到櫃檯結帳").count(), 0);
  await c.getByRole("button", { name: "送出訂單" }).scrollIntoViewIfNeeded();
  await c.screenshot({ path: join(shots, "2-no-refill-linepay-ok.png") });

  assert.deepEqual(errors, [], "No browser JavaScript errors");
  console.log(`refill-linepay smoke OK — screenshots in ${shots}`);
} finally {
  await browser.close();
}
