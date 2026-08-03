// 手冊 08f：POS 贈品與臨時折扣（docs/32）。
// 實測：加入兩樣商品 → 單品折扣 → 整單折扣 → 把一列改為贈品 → 現金結帳 →
// 到交易紀錄示範「退主商品但贈品未退」的提示。全程截圖供手冊使用。
import { existsSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import {
  apiJson,
  apiLogin,
  BASE,
  login,
  makeShot,
  note,
  SHOTS_ROOT,
  shotsDir,
  statePath,
} from "./_lib.mjs";

const dir = shotsDir("08-pos-gift-discount");
const shot = makeShot(dir);

const token = await apiLogin();
// 用手冊既有的一般商品；沒有的話直接停下，不自行造資料（手冊要照真實資料截圖）。
const products = (
  await apiJson(token, "GET", "/api/v1/catalog-products?limit=100")
).json.filter((p) => p.quantity_on_hand >= 3 && Number(p.unit_price) > 0);
if (products.length < 2) {
  throw new Error("需要至少兩個有庫存的一般商品，請先跑 06-inventory");
}
const [productA, productB] = products;
note(`使用商品：${productA.name} / ${productB.name}`);

// 沿用既有登入態；單獨執行本節（前面章節未跑）時自行登入。
const statefile = statePath("staff-state.json");
const hasState = existsSync(statefile);
const browser = await chromium.launch();
const ctx = await browser.newContext({
  ...(hasState ? { storageState: statefile } : {}),
  viewport: { width: 1440, height: 950 },
  deviceScaleFactor: 2,
  locale: "zh-TW",
  timezoneId: "Asia/Taipei",
});
const page = await ctx.newPage();
if (!hasState) await login(page);
page.on("pageerror", (e) => console.log(`⚠ POS JS 錯誤 ${e}`));

async function clearCart() {
  const removeButtons = page.locator('.pos-cart button:has-text("移除")');
  while ((await removeButtons.count()) > 0) {
    await removeButtons.first().click();
    await page.waitForTimeout(700);
  }
  await page.waitForTimeout(1200);
}

async function addBySku(sku, expectedRows) {
  await page.waitForFunction(
    () => document.querySelector('input[name="code"]')?.disabled === false,
    undefined,
    { timeout: 20000 },
  );
  await page.fill(".pos-scan-input", sku);
  await page.keyboard.press("Enter");
  await page.waitForSelector(`.pos-cart tbody tr:nth-child(${expectedRows})`, {
    timeout: 20000,
  });
  await page.waitForTimeout(1500);
}

await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await clearCart();

// ── 購物車：兩樣商品 ──
await addBySku(productA.sku, 1);
await addBySku(productB.sku, 2);
await shot(page, "cart", { locator: ".pos-grid" });

// ── 單品折扣 ──
await page.locator(`button[aria-label="折扣 ${productA.name}"]`).click();
await page.waitForSelector('[role="dialog"][aria-label="新增折扣"]');
await page.fill('input[aria-label="折扣數值"]', "50");
await page.waitForTimeout(600);
await shot(page, "item-discount-dialog", { locator: ".pos-dialog" });
await page.click('button:has-text("套用折扣")');
await page.waitForTimeout(2500);
await shot(page, "item-discount-applied", { locator: ".pos-right" });

// ── 整單折扣 ──
await page.click('button:has-text("整單折扣")');
await page.waitForSelector('[role="dialog"][aria-label="新增折扣"]');
await page.locator('input[type="radio"]').nth(1).check();
await page.fill('input[aria-label="折扣數值"]', "10");
await page.waitForTimeout(600);
await shot(page, "order-discount-dialog", { locator: ".pos-dialog" });
await page.click('button:has-text("套用折扣")');
await page.waitForTimeout(2500);
await shot(page, "discount-list", { locator: ".pos-discount-panel" });

// ── 改為贈品 ──
await page.locator(`button[aria-label="改為贈品 ${productB.name}"]`).click();
await page.waitForSelector('[role="dialog"][aria-label="改為贈品"]');
await page.selectOption('[role="dialog"] select', { index: 1 });
await page.waitForTimeout(600);
await shot(page, "gift-dialog", { locator: ".pos-dialog" });
await page.click('button:has-text("確認贈送")');
await page.waitForSelector(".pos-gift-badge");
await page.waitForSelector(".pos-summary", { timeout: 20000 });
await page.waitForTimeout(2000);
await shot(page, "gift-cart", { locator: ".pos-grid" });
await shot(page, "summary", { locator: ".pos-right" });

// ── 現金結帳 ──
await page.locator('.pos-tender-mode:has-text("現金")').first().click();
await page.waitForTimeout(1000);
await page.click('button:has-text("結帳")');
await page.waitForSelector(".pos-complete", { timeout: 30000 });
await page.waitForTimeout(1500);
await shot(page, "completed", { locator: ".pos-complete" });

// ── 交易紀錄：退主商品但贈品未退 ──
await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await page.locator("table tbody tr").first().locator('button:has-text("退貨")').click();
const dialog = page.locator('[role="dialog"]');
await dialog.waitFor({ state: "visible" });
await page.waitForTimeout(1500);
await dialog.locator(".return-qty-input").first().fill("1");
await dialog.locator('input[type="text"]').first().fill("尺寸不合");
await dialog.locator(".return-gift-notice").waitFor({ state: "visible", timeout: 20000 });
await page.waitForTimeout(1200);
await shot(page, "return-gift-notice", { locator: ".pos-dialog, .card" });

await browser.close();
note(`08f 完成，截圖於 ${join(SHOTS_ROOT, "08-pos-gift-discount")}`);
