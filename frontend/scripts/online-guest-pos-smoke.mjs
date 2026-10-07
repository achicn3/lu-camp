// 真客人頁 + 本機 Worker/D1 + backend/Postgres；只准對隔離測試環境執行。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium, devices } from "playwright";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const API = process.env.SMOKE_API ?? "http://localhost:8104";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8789";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/online-guest-pos");
mkdirSync(SHOTS, { recursive: true });
let token = "";
async function api(method, path, body) {
  const response = await fetch(`${API}/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.ok(response.ok, `${path}: ${response.status} ${await response.clone().text()}`);
  return response.json();
}
async function waitFor(fn, label) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const result = await fn();
    if (result) return result;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Timed out: ${label}`);
}
token = (await api("POST", "/auth/login", {
  username: process.env.SMOKE_USERNAME ?? "dev-manager",
  password: process.env.SMOKE_PASSWORD ?? "dev-test-123456",
})).access_token;
const run = randomUUID().slice(0, 6);
const name = `客人煙霧拿鐵-${run}`;
const note = `少冰-${run}`;
const item = await api("POST", "/menu-items", { name, unit_price: "150", category: "咖啡" });
await api("PATCH", `/menu-items/${item.id}`, { daily_limited: true });
await api("POST", `/menu-daily-stock/item/${item.id}/set`, { qty: 2, expected_remaining: 0 });
const settings = await api("GET", "/settings");
await api("PATCH", "/settings", { dine_in_tables: [...new Set([...(settings.dine_in_tables ?? []), "A1"])] });
await api("POST", "/online-order/publish");
const status = await api("GET", "/online-order/status");
const code = status.tables.find((table) => table.label === "A1")?.code;
assert.ok(code);
await api("PUT", "/online-orders/accepting", { accepting: true });
const browser = await chromium.launch();
const page = await browser.newPage({ ...devices["iPhone 13"] });
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
try {
  await page.goto(`${ORDER}/t/${code}`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "全部", exact: true }).click();
  await page.locator(".item", { hasText: name }).locator(".item-detail").click();
  await page.getByRole("button", { name: "加入購物車" }).click();
  await page.getByRole("button", { name: /購物車 1 份/ }).click();
  await page.locator("#order-note").fill(note);
  assert.equal(await page.locator("#footer").isVisible(), false, "Cart footer must not cover the form");
  await page.screenshot({ path: join(SHOTS, "01-cart.png"), fullPage: true });
  await page.getByRole("button", { name: "送出現金訂單" }).click();
  await page.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 45000 });
  await page.getByText("合計 $150").waitFor();
  assert.equal(await page.getByRole("button", { name: /取消/ }).count(), 0, "客人送單後不能自行取消");
  const guestCancel = await fetch(`${page.url().replace("/order/", "/api/orders/")}/cancel`, { method: "POST" });
  assert.equal(guestCancel.status, 405, "公開 API 不提供客人取消訂單");
  const order = await waitFor(async () => (await api("GET", "/online-orders")).orders.find((o) => o.note === note), "POS import");
  assert.equal(order.table_label, "A1");
  assert.equal(order.total, "150");
  assert.equal(order.lines[0].item_id, item.id);
  assert.equal(order.hold_status, "HELD");
  await page.screenshot({ path: join(SHOTS, "02-order.png"), fullPage: true });
  await api("POST", `/online-orders/${order.id}/cancel`);
  await page.getByText("這張訂單已取消。").waitFor({ timeout: 30000 });
  await page.screenshot({ path: join(SHOTS, "03-cancelled.png"), fullPage: true });
  await api("POST", `/menu-daily-stock/item/${item.id}/adjust`, { delta: -2, reason: "WASTE" });
  await page.getByRole("button", { name: "返回菜單", exact: true }).click();
  await page.locator(".item", { hasText: name }).locator(".item-badge-off").waitFor({ timeout: 30000 });
  await page.screenshot({ path: join(SHOTS, "04-sold-out.png"), fullPage: true });
  assert.deepEqual(errors, []);
  console.log(`PASS guest checkout → real POS hold → cancel → guest polling → live sold out. Screenshots: ${SHOTS}`);
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-error.png"), fullPage: true });
  console.error(await page.locator("body").innerText());
  throw error;
} finally {
  await browser.close();
}
