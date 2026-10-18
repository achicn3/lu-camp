// 客人頁品名（店主 2026-10-17）的真瀏覽器煙霧：體驗卡的行只顯示卡片名稱。
// 續點（要本桌點過才能勾，2026-10-19）另見 refill-table-smoke.mjs。
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
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "online-guest-names");
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
const card = `蜜桃體驗 ${run}`;
await integration("PUT", "/integration/menu", {
  version: run, published_at: new Date().toISOString(), store_name: "露坑", font: null, refill_pct: 80,
  categories: [{ id: 1, name: "咖啡" }, { id: 2, name: "甜點" }],
  items: [{
    id: 5, name: coffee, description: null, category_id: 1, unit_price: 200, photo: null,
    available: true, remaining: null, refillable: true,
    option_groups: [{ id: 8, name: "溫度", min_select: 1, max_select: 1, options: [
      { id: 9, name: "熱", price_delta: 0, available: true, remaining: null },
      { id: 10, name: "冰", price_delta: 0, available: true, remaining: null },
    ] }],
  }, {
    id: 6, name: `戚風 ${run}`, description: null, category_id: 2, unit_price: 100, photo: null,
    available: true, remaining: null, refillable: true, option_groups: [],
  }],
  experiences: [{
    id: 3, item_id: 5, option_ids: [], title: card, tag: null, origin: null, notes: null,
    description: null, includes: [], theme: "peach", art: "none", effect: "random",
  }],
});
await integration("PUT", "/integration/store-status", { accepting: true });
await integration("GET", "/integration/orders");

const browser = await chromium.launch();
const context = await browser.newContext({ ...devices["iPhone 13"] });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
// 重新整理後若停在購物車就不必再點（頁面會記得上次在購物車）。
async function openCart() {
  if (await page.locator("#cart-view").isHidden()) await page.getByRole("button", { name: /購物車 2 份/ }).click();
}
try {
  await page.goto(base, { waitUntil: "networkidle" });
  // 外帶（沒掃桌碼）不續點：可續點的品項也不標「可續點」（2026-10-19）。
  await page.getByRole("button", { name: "甜點", exact: true }).click();
  await page.locator(".item", { hasText: `戚風 ${run}` }).waitFor();
  assert.equal(await page.getByText("可續點 8 折").count(), 0, "外帶不提續點");

  // 體驗卡的行：購物車直接放一行（體驗卡的翻卡動畫不是這次要驗的）。
  await page.evaluate(({ saved }) => localStorage.setItem("lk_cart_v2", JSON.stringify(saved)), {
    saved: {
      lines: [
        { item_id: 6, option_ids: [], qty: 1 },
        { item_id: 5, option_ids: [10], qty: 1, experience_id: 3 },
      ],
      table: null, touched_at: Date.now(),
    },
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("button", { name: /購物車 2 份/ }).click();
  const names = await page.locator(".cart-line b").allTextContents();
  assert.deepEqual(names, [`戚風 ${run}`, `${card}（冰）`], "體驗卡只顯示卡片名稱＋客人選的選項");
  await page.screenshot({ path: join(shots, "2-cart.png"), fullPage: true });

  // 有行不能結（手沖剛售完）時改逐行顯示：體驗卡仍是卡片名稱＋客人選的（Codex 審查）。
  await integration("PUT", "/integration/menu/availability", {
    menu_version: run, revision: run,
    items: [{ id: 5, available: true, remaining: 0 }, { id: 6, available: true, remaining: null }],
    options: [{ id: 9, available: true, remaining: null }, { id: 10, available: true, remaining: null }],
  });
  await page.goto(base, { waitUntil: "networkidle" });
  await openCart();
  await page.locator(".cart-line b", { hasText: `${card}（冰）` }).waitFor();
  await page.screenshot({ path: join(shots, "2b-cart-needs-adjusting.png"), fullPage: true });
  await integration("PUT", "/integration/menu/availability", {
    menu_version: run, revision: run + 1,
    items: [{ id: 5, available: true, remaining: null }, { id: 6, available: true, remaining: null }],
    options: [{ id: 9, available: true, remaining: null }, { id: 10, available: true, remaining: null }],
  });
  await page.goto(base, { waitUntil: "networkidle" });
  await openCart();

  // 補貨後的供應每 15 秒更新一次，購物車恢復成可送出。
  await page.getByRole("button", { name: "送出訂單" }).click({ timeout: 30000 });
  await page.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 30000 });
  await page.getByText(`${card}（冰） × 1`).waitFor();
  assert.equal(await page.getByText(coffee).count(), 0, "訂單頁也只顯示卡片名稱");
  await page.screenshot({ path: join(shots, "3-order.png"), fullPage: true });

  // POS 拉到的品名照舊（店員要知道做哪一杯）。
  const pulled = await integration("GET", "/integration/orders");
  const order = pulled.orders.find((o) => o.lines.some((l) => l.name.includes(String(run))));
  assert.deepEqual(order.lines.map((l) => l.name), [`戚風 ${run}`, `${card}・${coffee}（冰）`]);

  assert.deepEqual(errors, [], "No browser JavaScript errors");
  console.log(`guest-names smoke OK — screenshots in ${shots}`);
} catch (error) {
  await page.screenshot({ path: join(shots, "error.png"), fullPage: true }).catch(() => {});
  console.error("Page state:", await page.locator("body").innerText().catch(() => "unavailable"));
  throw error;
} finally {
  await browser.close();
}
