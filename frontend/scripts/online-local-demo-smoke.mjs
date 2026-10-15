// Real local demo: Turnstile in browser → Worker → Postgres → POS, then cancel only this test order.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1");
const API = "http://localhost:8116/api/v1", POS = "http://localhost:3116", ORDER = "http://localhost:8794";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-local-demo-shots";
mkdirSync(SHOTS, { recursive: true });
let token = "", orderId;
async function api(method, path, body) {
  const r = await fetch(API + path, { method, headers: { Connection: "close", "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: body === undefined ? undefined : JSON.stringify(body) });
  assert.ok(r.ok, `${path}: ${r.status}`); return r.json();
}
token = (await api("POST", "/auth/login", { username: "dev-manager", password: process.env.SMOKE_PASSWORD ?? "dev-test-123456" })).access_token;
const browser = await chromium.launch();
try {
  const pos = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  await skipOpeningCheckRedirect(pos);
  await pos.goto(`${POS}/login`);
  await pos.locator('input[name="username"]').fill("dev-manager");
  await pos.locator('input[name="password"]').fill(process.env.SMOKE_PASSWORD ?? "dev-test-123456");
  await pos.getByRole("button", { name: "登入", exact: true }).click();
  await pos.waitForURL(url => !url.pathname.startsWith("/login"));
  await pos.goto(`${POS}/menu`);
  await pos.getByRole("button", { name: "拿鐵 線上呈現", exact: true }).click();
  const dialog = pos.getByRole("dialog", { name: "拿鐵 的線上呈現", exact: true });
  const role = dialog.getByLabel("商品類型（搭配推薦用）");
  await role.selectOption("coffee");
  await dialog.getByText(/客人點甜點時，這杯咖啡可出現在搭配推薦中/).waitFor();
  await pos.screenshot({ path: `${SHOTS}/role-desktop.png` });
  await pos.setViewportSize({ width: 390, height: 844 });
  await role.selectOption("dessert");
  await dialog.getByText(/客人點咖啡時，這份甜點可出現在搭配推薦中/).waitFor();
  assert.ok(await pos.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.ok(await dialog.locator(".menu-presentation-dialog").evaluate(node => node.scrollWidth <= node.clientWidth), "mobile explanation must not be clipped horizontally");
  await pos.screenshot({ path: `${SHOTS}/role-mobile.png` });
  await dialog.getByRole("button", { name: "關閉", exact: true }).click(); // do not change user's settings
  const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await guest.goto(ORDER);
  await guest.getByRole("button", { name: "甜點", exact: true }).click();
  const item = guest.locator(".item").filter({ hasText: "巴斯克蛋糕" });
  await item.locator(".item-add").click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  const resultPromise = guest.waitForResponse(r => r.url() === `${ORDER}/api/orders` && r.request().method() === "POST", { timeout: 90000 });
  await guest.getByRole("button", { name: "送出訂單", exact: true }).click();
  const result = await resultPromise;
  assert.equal(result.status(), 201, "Real browser Turnstile must allow a new order");
  await guest.waitForURL(/\/order\//);
  const created = await result.json();
  const publicOrder = await (await fetch(`${ORDER}/api/orders/${created.token}`)).json();
  let received;
  for (let n = 0; n < 40; n++) {
    received = (await api("GET", "/online-orders")).orders.find(o => Date.parse(o.created_at) === Date.parse(publicOrder.created_at) && o.total === "120");
    if (received) break;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  assert.ok(received, "Submitted order must reach POS"); orderId = received.id;
  assert.equal(received.lines[0].name, "巴斯克蛋糕");
  await guest.screenshot({ path: `${SHOTS}/order-success.png` });
  await pos.setViewportSize({ width: 1280, height: 1000 });
  await pos.goto(`${POS}/pos`);
  await pos.getByRole("button", { name: /線上訂單/ }).click();
  await pos.locator(".online-order").filter({ hasText: "巴斯克蛋糕" }).first().waitFor();
  await pos.screenshot({ path: `${SHOTS}/pos-received.png` });
  console.log("PASS real browser verification → order 201 → POS $120; role help desktop/mobile");
} finally {
  if (orderId) await api("POST", `/online-orders/${orderId}/cancel`);
  await browser.close();
}
