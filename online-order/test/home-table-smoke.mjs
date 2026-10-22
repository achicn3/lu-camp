// Real local Worker + D1: after a table order, opening the order page in another browser
// (LINE Pay often returns into LINE's in-app browser) and pressing 回到首頁 lands on the same
// table, not takeout. Run against an isolated wrangler dev with Cloudflare's test Turnstile keys.
import assert from "node:assert/strict";
import { createHash, createHmac, randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium, devices } from "../../frontend/node_modules/playwright/index.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "Set SMOKE_ALLOW_WRITE=1 for the isolated local Worker");
const base = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8787";
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):/, "Local Worker only — never production");
const secret = process.env.SMOKE_SECRET;
assert.ok(secret, "Set SMOKE_SECRET to the local Worker's INTEGRATION_SECRET");
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "online-home-table");
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
      "Content-Type": "application/json",
      "X-LuCamp-Timestamp": timestamp, "X-LuCamp-Nonce": nonce, "X-LuCamp-Signature": signature,
    },
    body: method === "GET" ? undefined : body,
  });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${await response.text()}`);
  return response.json();
}

const status = await (await fetch(`${base}/api/status`)).json();
assert.equal(status.turnstile_site_key, "1x00000000000000000000AA", "Use Cloudflare's test sitekey locally");
const run = Date.now();
const table = `hm${run}Tbl0000`.slice(0, 20);
await integration("PUT", "/integration/menu", {
  version: run, published_at: new Date().toISOString(), store_name: "露坑", font: null,
  categories: [{ id: 1, name: "咖啡" }],
  items: [{
    id: 5, name: `拿鐵 ${run}`, description: "煙霧測試品項", category_id: 1,
    unit_price: 150, photo: null, available: true, remaining: null, option_groups: [],
  }],
});
await integration("PUT", "/integration/tables", {
  revision: run, tables: [{ code: table, label: "A9", service_mode: "DINE_IN" }],
});
await integration("PUT", "/integration/store-status", { accepting: true });
await integration("GET", "/integration/orders");

const browser = await chromium.launch();
const errors = [];
const phone = async () => {
  const context = await browser.newContext({ ...devices["iPhone 13"] });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(String(error)));
  return page;
};
const scanned = await phone();
let current = scanned;
try {
  await scanned.goto(`${base}/t/${table}`, { waitUntil: "networkidle" });
  await scanned.getByText("桌 A9").waitFor();
  await scanned.getByRole("button", { name: "咖啡", exact: true }).click();
  await scanned.locator(".item", { hasText: `拿鐵 ${run}` }).locator(".item-detail").click();
  await scanned.getByRole("button", { name: "加入購物車" }).click();
  await scanned.getByRole("button", { name: /購物車 1 份/ }).click();
  await scanned.getByRole("button", { name: "送出訂單" }).click();
  await scanned.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 30000 });
  const orderUrl = scanned.url();

  // 另一個瀏覽器（沒有掃碼時記下的桌號）打開同一張訂單頁，按「回到首頁」。
  current = await phone();
  await current.goto(orderUrl, { waitUntil: "networkidle" });
  await current.getByText("桌號 A9").waitFor();
  await current.screenshot({ path: join(shots, "order-other-browser.png"), fullPage: true });
  await current.locator(".order-home").click();
  await current.waitForURL(`${base}/t/${table}`, { timeout: 15000 });
  await current.getByText("桌 A9").waitFor();
  await current.screenshot({ path: join(shots, "home-same-table.png"), fullPage: true });

  // 左上 logo 也一樣回同一桌。
  current = await phone();
  await current.goto(orderUrl, { waitUntil: "networkidle" });
  await current.getByText("桌號 A9").waitFor();
  await current.locator("#home").click();
  await current.waitForURL(`${base}/t/${table}`, { timeout: 15000 });

  // 桌碼重發後，舊單的回首頁改回外帶首頁（不帶去失效的桌）。
  await integration("PUT", "/integration/tables", {
    revision: run + 1, tables: [{ code: `${table}New`, label: "A9", service_mode: "DINE_IN" }],
  });
  current = await phone();
  await current.goto(orderUrl, { waitUntil: "networkidle" });
  await current.getByText("桌號 A9").waitFor();
  await current.locator(".order-home").click();
  await current.waitForURL(`${base}/`, { timeout: 15000 });
  await current.screenshot({ path: join(shots, "home-after-reissue.png"), fullPage: true });

  assert.deepEqual(errors, [], "No browser JavaScript errors");
  console.log(`Home-table smoke passed. Screenshots: ${shots}`);
} catch (error) {
  await current.screenshot({ path: join(shots, "error.png"), fullPage: true }).catch(() => {});
  console.error("Page state:", await current.locator("body").innerText().catch(() => "unavailable"));
  console.error("Browser errors:", errors);
  throw error;
} finally {
  await browser.close();
}
