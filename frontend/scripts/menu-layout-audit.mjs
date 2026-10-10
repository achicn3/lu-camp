// Read-only layout edge cases: actual guest app/assets, mocked GET responses only.
// Complements menu-typography-smoke.mjs against the real published POS menu.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8789";
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/menu-layout-audit");
mkdirSync(shots, { recursive: true });
const published = await (await fetch(`${ORDER}/api/menu`)).json();
const longCategory = "Specialty" + "W".repeat(40);
const longName = "Coffee".repeat(18);
const longOption = "Temperature".repeat(4);
const presentation = { flavor_description: "Coffee".repeat(15), audience_description: "甜點與咖啡", is_new: true, limited_on: null, show_remaining: true, low_stock_threshold: 5, hide_sold_out: false };
const base = { id: 1, name: longName, description: "Description".repeat(25), category_id: 1, unit_price: 150, photo: null, available: true, remaining: 5, option_groups: [], presentation };
const menu = { ...published, categories: [{ id: 1, name: longCategory }, { id: 2, name: "甜點" }], items: [base,
  { ...base, id: 2, category_id: 2, name: "咖啡", option_groups: [{ id: 1, name: longOption, min_select: 1, max_select: 1, options: [{ id: 1, name: longOption, price_delta: 10, available: true, remaining: 3 }, { id: 2, name: "售完", price_delta: 0, available: false, remaining: 0 }] }] },
  { ...base, id: 3, name: "今日售完", remaining: 0 }] };
const browser = await chromium.launch();
const issues = [], errors = [];
let states = 0;
async function check(page, label, width, capture = false) {
  states++;
  const problems = await page.evaluate(() => {
    const ui = getComputedStyle(document.body).fontFamily;
    const hand = getComputedStyle(document.querySelector(".bar-name b")).fontFamily;
    const problems = [];
    if (document.documentElement.scrollWidth > innerWidth) problems.push(`page overflow ${document.documentElement.scrollWidth}/${innerWidth}`);
    for (const node of document.querySelectorAll("h1,h2,p,legend,.opt-choice,.tab,.shortcut,.sheet,.opt-group")) {
      if (!node.getClientRects().length) continue;
      const rect = node.getBoundingClientRect();
      if (rect.left < -1 || rect.right > innerWidth + 1 || node.scrollWidth > node.clientWidth + 1) problems.push(`overflow ${node.className || node.tagName}`);
    }
    for (const node of document.querySelectorAll("button:not(.tab):not(.shortcut),input,textarea,.item-price,.item-labels,.item-badge,.opt-group-name,.opt-choice,.cart-total,.order-line,.order-hint,.order-state,.empty-state,.field-error,.message")) {
      if (!node.getClientRects().length) continue;
      if (getComputedStyle(node).fontFamily !== ui) problems.push(`UI font ${node.className || node.tagName}`);
    }
    for (const node of document.querySelectorAll("h1,h2,.item-name,.cart-line b,.tab,.shortcut,.item-flavor,.item-audience,.item-desc,.sheet-desc")) {
      if (node.getClientRects().length && getComputedStyle(node).fontFamily !== hand) problems.push(`display font ${node.className || node.tagName}`);
    }
    return [...new Set(problems)];
  });
  issues.push(...problems.map((problem) => `${width}/${label}: ${problem}`));
  if (capture) await page.screenshot({ path: join(shots, `${label}-${width}.png`), fullPage: true });
}
try {
  for (const { width, fallback } of [{ width: 375 }, { width: 390 }, { width: 430 }, { width: 390, fallback: true }]) {
    const page = await browser.newPage({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true });
    page.on("pageerror", (error) => errors.push(String(error)));
    let currentMenu = menu, orderStatus = "HELD", menuHttpStatus = 200, validTable = true;
    await page.route("**/api/**", (route) => {
      assert.equal(route.request().method(), "GET", "Audit must never submit an order");
      const path = new URL(route.request().url()).pathname;
      const body = path === "/api/menu" ? currentMenu : path === "/api/status" ? { accepting: true } : path.startsWith("/api/tables/") && validTable ? { label: "W".repeat(20), service_mode: "DINE_IN" } : path.startsWith("/api/orders/") && orderStatus !== "MISSING" ? {
        status: orderStatus, table_label: "A1", service_mode: "DINE_IN", total: 150, note: "Note".repeat(15), created_at: new Date().toISOString(), lines: [{ name: longName, qty: 1, line_total: 150 }],
      } : null;
      return route.fulfill({ status: path === "/api/menu" ? menuHttpStatus : body ? 200 : 404, contentType: "application/json", body: JSON.stringify(body) });
    });
    if (fallback) await page.route("**/fonts/*.woff2", (route) => route.abort());
    await page.goto(`${ORDER}/t/${"b".repeat(16)}`, { waitUntil: "networkidle" });
    if (published.font && !fallback) await page.waitForFunction(() => document.documentElement.classList.contains("hand-font-ready"));
    const capture = width === 390 && !fallback;
    await check(page, "home", width, capture);
    await page.locator("#tabs").getByRole("button", { name: longCategory, exact: true }).click();
    await check(page, "category", width, !fallback);
    await page.locator('#list .item[data-item-id="1"] .item-detail').click();
    await check(page, "detail", width, capture);
    await page.getByRole("button", { name: "關閉", exact: true }).click();
    await page.locator("#tabs").getByRole("button", { name: "甜點", exact: true }).click();
    await page.locator('#list .item[data-item-id="2"] .item-detail').click();
    await page.locator("#detail-add").click();
    await page.getByText("請依每組規則選好選項。", { exact: true }).waitFor();
    await check(page, "options-error", width, capture);
    await page.getByRole("button", { name: "關閉", exact: true }).click();
    await page.locator("#tabs").getByRole("button", { name: longCategory, exact: true }).click();
    await page.locator('#list .item[data-item-id="3"] .item-detail').click();
    assert.equal(await page.locator("#detail-add").isDisabled(), true);
    await check(page, "sold-out", width, capture);
    await page.getByRole("button", { name: "關閉", exact: true }).click();
    await page.locator("#footer button").click();
    await check(page, "empty-cart", width, capture);
    await page.getByRole("button", { name: "← 返回菜單" }).click();
    await page.locator('#list .item[data-item-id="1"] .item-add').click();
    await page.locator("#footer button").click();
    await check(page, "cart", width, capture);
    currentMenu = { ...menu, items: menu.items.map((item) => ({ ...item, remaining: 0 })) };
    await page.reload({ waitUntil: "networkidle" });
    await check(page, "invalid-cart", width, capture);
    for (const status of ["HOLD_REQUESTED", "HELD", "UNPAID", "PAID", "REJECTED", "CANCELLED", "REFUNDED", "PARTIALLY_REFUNDED"]) {
      orderStatus = status;
      await page.goto(`${ORDER}/order/${"a".repeat(32)}`, { waitUntil: "networkidle" });
      await page.locator(".order-state").waitFor();
      await check(page, `order-${status}`, width, capture && status === "HELD");
    }
    orderStatus = "MISSING";
    await page.goto(`${ORDER}/order/${"a".repeat(32)}`, { waitUntil: "networkidle" });
    await page.getByText("暫時查不到訂單，請稍後再試。", { exact: true }).waitFor();
    await check(page, "missing-order", width, capture);
    await page.evaluate(() => localStorage.clear());
    currentMenu = { ...menu, items: [], categories: [] };
    await page.goto(ORDER, { waitUntil: "networkidle" });
    await check(page, "empty-menu", width, capture);
    for (const code of [404, 503]) {
      menuHttpStatus = code;
      await page.reload({ waitUntil: "networkidle" });
      await page.locator("#message").waitFor();
      await check(page, `menu-error-${code}`, width, capture);
    }
    menuHttpStatus = 200; currentMenu = menu; validTable = false;
    await page.goto(`${ORDER}/t/${"b".repeat(16)}`, { waitUntil: "networkidle" });
    await page.getByText("這個 QR 已經失效了，請洽櫃台。", { exact: true }).waitFor();
    await check(page, "invalid-qr", width, capture);
    await page.close();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(issues, []);
  console.log(`PASS ${states} read-only layout states across 375/390/430px and font fallback. Screenshots: ${shots}`);
} catch (error) {
  console.error(issues);
  throw error;
} finally { await browser.close(); }
