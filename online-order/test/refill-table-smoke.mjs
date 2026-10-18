// 續點要「本桌 90 分鐘內點過」（店主 2026-10-19）真瀏覽器煙霧：本機 Worker/D1＋真客人頁。
// 手機 A 在 T1 點手沖 → POS 結帳回報已付款 → 手機 B（另一支手機）在 T1：手沖可勾「我是續點」、
// 戚風（本桌還沒點過）只說明要先點過；送出續點單成功。外帶桌不提續點。
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
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "online-refill-table");
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
const cake = `戚風 ${run}`;
const T1 = `smokeT1${run}`.padEnd(16, "x");
const TO = `smokeTO${run}`.padEnd(16, "x");
await integration("PUT", "/integration/menu", {
  version: run, published_at: new Date().toISOString(), store_name: "露坑", font: null, refill_pct: 80,
  categories: [{ id: 1, name: "咖啡" }, { id: 2, name: "甜點" }],
  items: [
    { id: 5, name: coffee, description: null, category_id: 1, unit_price: 200, photo: null,
      available: true, remaining: null, refillable: true, option_groups: [] },
    { id: 6, name: cake, description: null, category_id: 2, unit_price: 100, photo: null,
      available: true, remaining: null, refillable: true, option_groups: [] },
  ],
});
await integration("PUT", "/integration/tables", { revision: run, tables: [
  { code: T1, label: "T1", service_mode: "DINE_IN" },
  { code: TO, label: "外帶", service_mode: "TAKEOUT" },
] });
await integration("PUT", "/integration/store-status", { accepting: true });
await integration("GET", "/integration/orders");

const browser = await chromium.launch();
const errors = [];
async function phone() {
  const context = await browser.newContext({ ...devices["iPhone 13"] });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(String(error)));
  return page;
}
async function openItem(page, tab, name) {
  await page.getByRole("button", { name: tab, exact: true }).click();
  await page.locator(".item", { hasText: name }).locator(".item-detail").click();
}
try {
  // 手機 A：T1 點一杯手沖、送出（櫃檯結帳）
  const a = await phone();
  await a.goto(`${base}/t/${T1}`, { waitUntil: "networkidle" });
  await a.getByRole("button", { name: "咖啡", exact: true }).click();
  const coffeeCard = a.locator(".item", { hasText: coffee });
  await coffeeCard.getByText("可續點 8 折").waitFor();
  await openItem(a, "咖啡", coffee);
  await a.getByText("本桌 90 分鐘內點過這項，才能用續點價（8 折）再點。").waitFor();
  assert.equal(await a.locator("#detail-refill:visible").count(), 0, "本桌還沒點過：沒有續點勾選");
  await a.screenshot({ path: join(shots, "1-not-ordered-yet.png") });
  await a.getByRole("button", { name: "加入購物車" }).click();
  await a.getByRole("button", { name: /購物車 1 份/ }).click();
  await a.getByRole("button", { name: "送出訂單" }).click();
  await a.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 30000 });

  // POS 拉到、在櫃檯收完錢 → 回報已付款
  const pulled = await integration("GET", "/integration/orders");
  const first = pulled.orders.find((o) => o.lines.some((l) => l.name === coffee));
  assert.ok(first, "POS 拉到手機 A 的單");
  await integration("POST", `/integration/orders/${first.id}/status`, { sync_status: "IMPORTED" });
  await integration("POST", `/integration/orders/${first.id}/status`, { sync_status: "SETTLED", payment_status: "PAID" });

  // 手機 B（另一支手機）同一桌：手沖可以續點；戚風本桌沒點過只說明
  const b = await phone();
  await b.goto(`${base}/t/${T1}`, { waitUntil: "networkidle" });
  await openItem(b, "咖啡", coffee);
  await b.getByLabel("我是續點（8 折，加料也打折）").check();
  await b.getByText("本桌點過這項，可以用續點價再點。").waitFor();
  assert.equal(await b.getByText(/續杯/).count(), 0, "畫面不出現「續杯」");
  await b.screenshot({ path: join(shots, "2-other-phone-eligible.png") });
  await b.getByRole("button", { name: "加入購物車" }).click();
  await openItem(b, "甜點", cake);
  await b.getByText("本桌 90 分鐘內點過這項，才能用續點價（8 折）再點。").waitFor();
  await b.locator("#sheet-close").click();
  await b.getByRole("button", { name: /購物車 1 份/ }).click();
  await b.locator(".cart-line b", { hasText: `續點・${coffee}` }).waitFor();
  await b.getByRole("button", { name: "送出訂單" }).click();
  await b.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 30000 });
  await b.getByText(`續點・${coffee} × 1`).waitFor();
  await b.screenshot({ path: join(shots, "3-refill-order.png") });

  // 外帶桌：不提續點
  const c = await phone();
  await c.goto(`${base}/t/${TO}`, { waitUntil: "networkidle" });
  await c.getByRole("button", { name: "咖啡", exact: true }).click();
  await c.locator(".item", { hasText: coffee }).waitFor();
  assert.equal(await c.getByText("可續點 8 折").count(), 0, "外帶不提續點");

  assert.deepEqual(errors, [], "No browser JavaScript errors");
  console.log(`refill-table smoke OK — screenshots in ${shots}`);
} finally {
  await browser.close();
}
