// Published POS menu, real Worker: shared type roles must match across home/category/cart.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8789";
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/menu-typography");
mkdirSync(shots, { recursive: true });
const snapshot = await (await fetch(`${ORDER}/api/menu`)).json();
const recommendations = snapshot.items.filter((entry) => entry.presentation?.is_recommended && entry.available && entry.remaining !== 0 &&
  entry.option_groups.every((group) => group.max_select >= group.min_select && group.options.filter((option) => option.available && option.remaining !== 0).length >= group.min_select)).slice(0, 3);
const item = recommendations.find((entry) => entry.option_groups.length === 0);
assert.ok(item, "The published menu must include an available POS item without options among the first three recommendations");
const browser = await chromium.launch();
const errors = [];
async function typeOf(locator) {
  return locator.evaluate((node) => {
    const css = getComputedStyle(node);
    return { family: css.fontFamily, size: css.fontSize, lineHeight: css.lineHeight, style: css.fontStyle };
  });
}
try {
  for (const { width, fallback } of [{ width: 375 }, { width: 390 }, { width: 430 }, { width: 390, fallback: true }]) {
    const label = `${width}${fallback ? "-fallback" : ""}`;
    const phone = await browser.newPage({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true });
    phone.on("pageerror", (error) => errors.push(String(error)));
    if (fallback) await phone.route("**/fonts/*.woff2", (route) => route.abort());
    await phone.goto(ORDER, { waitUntil: "networkidle" });
    if (snapshot.font && !fallback) await phone.waitForFunction(() => document.documentElement.classList.contains("hand-font-ready"));
    const homeCard = phone.locator(`#recommended-list .item[data-item-id="${item.id}"]`);
    await homeCard.waitFor();
    if (fallback) assert.equal(await phone.locator("html").evaluate((node) => node.classList.contains("hand-font-ready")), false);
    const homeName = await typeOf(homeCard.locator(".item-name"));
    const price = await typeOf(homeCard.locator(".item-price"));
    const add = await typeOf(homeCard.locator(".item-add"));
    assert.equal(price.family, add.family, "Prices and controls must share the UI font, without a mismatched italic baseline");
    assert.equal(price.style, "normal");
    const nameBounds = await homeCard.locator(".item-name").boundingBox();
    const priceBounds = await homeCard.locator(".item-price").boundingBox();
    assert.ok(Math.abs(nameBounds.x - priceBounds.x) < 1, "Name and price must share the card left edge");
    await phone.screenshot({ path: join(shots, `home-${label}.png`), fullPage: true });
    await phone.locator("#tabs").getByRole("button", { name: "全部", exact: true }).click();
    const card = phone.locator(`#list .item[data-item-id="${item.id}"]`);
    assert.deepEqual(await typeOf(card.locator(".item-name")), homeName);
    assert.deepEqual(await typeOf(card.locator(".item-price")), price);
    await card.locator(".item-add").click();
    await phone.screenshot({ path: join(shots, `category-${label}.png`) });
    await phone.getByRole("button", { name: /購物車 1 份/ }).click();
    assert.deepEqual(await typeOf(phone.locator(".cart-line b")), homeName, "The same product name must retain its type role in cart");
    assert.deepEqual(await typeOf(phone.locator(".cart-line .item-price")), price);
    for (const selector of [".qty-button", ".quiet-action", ".cart-controls > span", ".cart-total", ".note-label", ".payment-note", "#cart-body > .action"]) {
      assert.equal((await typeOf(phone.locator(selector).first())).family, add.family, `${selector} must use the shared UI font`);
    }
    const total = await typeOf(phone.locator(".cart-total"));
    assert.equal(total.style, "normal");
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await phone.locator(".cart-controls").evaluate((node) => {
      const heights = [...node.children].map((child) => child.getBoundingClientRect());
      return Math.max(...heights.map((r) => r.y + r.height / 2)) - Math.min(...heights.map((r) => r.y + r.height / 2)) < 1;
    }), true, "Quantity text and buttons must share a vertical center");
    await phone.screenshot({ path: join(shots, `cart-${label}.png`), fullPage: true });
    await phone.getByRole("button", { name: "← 返回菜單", exact: true }).click();
    await phone.locator("#list").waitFor();
    await card.locator(".item-detail").click();
    assert.equal((await typeOf(phone.locator("#detail-add"))).family, add.family);
    assert.equal((await typeOf(phone.locator(".qty-input"))).family, add.family);
    await phone.getByRole("button", { name: "關閉", exact: true }).click();
    await phone.close();
  }
  assert.deepEqual(errors, []);
  console.log(`PASS consistent type roles and quantity alignment across home/category/cart at 375/390/430px, including font-load failure. Screenshots: ${shots}`);
} finally { await browser.close(); }
