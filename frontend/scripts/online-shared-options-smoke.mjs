// True POS API + Postgres + local Worker/D1, mobile guest checkout with shared options.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1");
const API = process.env.SMOKE_API ?? "http://127.0.0.1:8116";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8794";
const POS = process.env.SMOKE_BASE ?? "http://127.0.0.1:3116";
for (const base of [API, ORDER, POS]) {
  const u = new URL(base);
  assert.equal(u.protocol, "http:");
  assert.ok(["127.0.0.1", "localhost"].includes(u.hostname));
}
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lukeng-shared-options";
mkdirSync(SHOTS, { recursive: true });
let token = "";
async function api(method, path, body) {
  const r = await fetch(`${API}/api/v1${path}`, { method,
    headers: { Connection: "close", Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  assert.ok(r.ok, `${path}: ${r.status}`); return r.json();
}
async function until(fn) {
  for (let n = 0; n < 80; n++) { const v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, 500)); }
  throw new Error("Timed out waiting for POS/cloud sync");
}
token = (await api("POST", "/auth/login", { username: "dev-manager", password: process.env.SMOKE_PASSWORD ?? "dev-test-123456" })).access_token;
const run = randomUUID().slice(0, 6);
const required = await api("POST", "/menu-option-groups", { name: `溫度-${run}`, min_select: 1, max_select: 1,
  options: [{ name: "熱", price_delta: "0" }, { name: "冰", price_delta: "0" }] });
const optional = await api("POST", "/menu-option-groups", { name: `加購-${run}`, min_select: 0, max_select: 1,
  options: [{ name: "燕麥奶", price_delta: "20" }, { name: "售完配料", price_delta: "30" }] });
await api("PATCH", `/menu-options/${optional.options[1].id}`, { is_available: false });
const fixtures = [];
for (const width of [375, 390, 430]) {
  const item = await api("POST", "/menu-items", { name: `共享拿鐵-${run}-${width}`, unit_price: "150", category: "選項驗收" });
  await api("PUT", `/menu-items/${item.id}/option-groups`, { group_ids: [required.id, optional.id] });
  fixtures.push({ width, item });
}
await api("POST", "/online-order/publish");
await api("PUT", "/online-orders/accepting", { accepting: true });
await until(async () => (await (await fetch(`${ORDER}/api/status`)).json()).accepting);
const snapshot = await (await fetch(`${ORDER}/api/menu`)).json();
for (const { item } of fixtures) {
  const online = snapshot.items.find(i => i.id === item.id);
  assert.deepEqual(online.option_groups.map(g => [g.id, g.min_select, g.max_select]), [[required.id, 1, 1], [optional.id, 0, 1]]);
  assert.equal(online.option_groups[1].options[0].price_delta, 20);
}
const browser = await chromium.launch();
const cleanup = [];
try {
  for (const { width, item } of fixtures) {
    const context = await browser.newContext({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true });
    const page = await context.newPage();
    await page.goto(ORDER, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "選項驗收", exact: true }).click();
    await page.locator(`.item[data-item-id="${item.id}"] .item-detail`).click();
    const sheet = page.locator("#sheet-body");
    await sheet.locator("#detail-add").click();
    assert.equal(await sheet.getByRole("alert").innerText(), "請依每組規則選好選項。", "Required POS group must be enforced");
    await sheet.locator(`input[value="${required.options[0].id}"]`).check();
    await sheet.locator(`input[value="${optional.options[0].id}"]`).check();
    await sheet.getByLabel("不選擇", { exact: true }).check();
    // Keep the sheet open through the real 15-second menu refresh.
    const refresh = await page.waitForResponse(response => response.url() === `${ORDER}/api/menu`);
    await refresh.finished();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await sheet.getByLabel("不選擇", { exact: true }).isEnabled(), true, "Clear selection must remain usable after refresh");
    assert.equal(await sheet.getByLabel("不選擇", { exact: true }).isChecked(), true);
    assert.equal(await sheet.locator(`input[value="${optional.options[0].id}"]`).isChecked(), false);
    assert.equal(await sheet.locator(`input[value="${optional.options[1].id}"]`).isDisabled(), true);
    await sheet.locator(`input[value="${optional.options[0].id}"]`).check();
    await page.screenshot({ path: `${SHOTS}/options-${width}.png`, fullPage: true });
    await sheet.locator("#detail-add").click();
    // A second line must really reach POS without the previously selected surcharge.
    await page.locator(`.item[data-item-id="${item.id}"] .item-detail`).click();
    await sheet.locator(`input[value="${required.options[0].id}"]`).check();
    await sheet.locator(`input[value="${optional.options[0].id}"]`).check();
    await sheet.getByLabel("不選擇", { exact: true }).check();
    await sheet.locator("#detail-add").click();
    await page.getByRole("button", { name: /購物車 2 份/ }).click();
    assert.equal(await page.locator("#order-note").count(), 0);
    assert.ok((await page.locator("#cart-body").innerText()).includes("$170"));
    assert.ok((await page.locator("#cart-body").innerText()).includes("$150"));
    assert.equal(await page.locator(".cart-total").innerText(), "合計 $320");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: `${SHOTS}/cart-${width}.png`, fullPage: true });
    await page.getByRole("button", { name: "送出訂單", exact: true }).click();
    await page.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 45000 });
    const order = await until(async () => (await api("GET", "/online-orders")).orders.find(o => o.lines.some(l => l.item_id === item.id)));
    cleanup.push(order.id);
    assert.ok(!("note" in order));
    assert.equal(order.total, "320");
    assert.equal(order.lines.length, 2);
    assert.deepEqual(order.lines[0].option_ids, [required.options[0].id, optional.options[0].id]);
    assert.deepEqual(order.lines[1].option_ids, [required.options[0].id]);
    const cart = await api("GET", `/online-orders/${order.id}/cart`);
    assert.equal(cart.total, "320");
    assert.deepEqual(cart.lines.map(l => l.unit_price), ["170", "150"]);
    const posContext = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    const pos = await posContext.newPage();
    await skipOpeningCheckRedirect(pos);
    await pos.goto(`${POS}/login`, { waitUntil: "networkidle" });
    await pos.fill('input[name="username"]', "dev-manager");
    await pos.fill('input[name="password"]', process.env.SMOKE_PASSWORD ?? "dev-test-123456");
    await pos.getByRole("button", { name: "登入", exact: true }).click();
    await pos.waitForURL(url => !url.pathname.startsWith("/login"));
    await pos.goto(`${POS}/pos`, { waitUntil: "networkidle" });
    await pos.getByRole("button", { name: /線上訂單/ }).click();
    const orders = pos.getByRole("dialog", { name: "線上訂單" });
    const row = orders.getByRole("listitem", { name: "外帶 $320", exact: true }).filter({ hasText: item.name });
    assert.ok((await row.innerText()).includes("$320"));
    await pos.screenshot({ path: `${SHOTS}/pos-order-${width}.png` });
    await row.getByRole("button", { name: "帶入結帳" }).click();
    await orders.waitFor({ state: "detached" });
    const posLines = pos.locator(".pos-cart tbody tr");
    await posLines.nth(1).waitFor();
    assert.equal(await posLines.count(), 2);
    assert.match(await posLines.nth(0).innerText(), /燕麥奶/);
    assert.match(await posLines.nth(0).innerText(), /170/);
    assert.doesNotMatch(await posLines.nth(1).innerText(), /燕麥奶/);
    assert.match(await posLines.nth(1).innerText(), /150/);
    await pos.screenshot({ path: `${SHOTS}/pos-cart-${width}.png` });
    await posContext.close();
    console.log(`PASS ${width}: guest 170+150=320; POS UI two lines, cleared surcharge absent`);
    await context.close();
  }
  await api("PATCH", `/menu-options/${optional.options[0].id}`, { is_available: false });
  await until(async () => {
    const menu = await (await fetch(`${ORDER}/api/menu`)).json();
    return menu.items.find(i => i.id === fixtures[0].item.id).option_groups[1].options[0].available === false;
  });
  console.log(`PASS 375/390/430: shared POS group IDs, required options, optional clear, 170+150=320, sold-out option, no note, actual POS UI/import/cart, live stop-sale sync. Screenshots: ${SHOTS}`);
} finally {
  await browser.close();
  for (const id of cleanup) await api("POST", `/online-orders/${id}/cancel`);
}
