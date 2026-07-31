// 手冊 06：庫存——序號品 / 久滯庫存 / 一般商品 / 散裝批 四分頁、篩選查詢、明細、改價、補印標籤、上架一般商品。
import { writeFileSync } from "node:fs";
import { join } from "node:path";

import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("06-inventory");
const shot = makeShot(dir);
const { browser, page } = await newBrowser();
await login(page);

await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await shot(page, "serialized-list", { content: true });
await shot(page, "tabs", { locator: ".inv-tabs, .inv-panel >> nth=0" }).catch(() => {});
await shot(page, "filters", { locator: ".inv-filters" });

// 篩選：狀態＝在庫、搜尋品名
await page.locator('select[aria-label="狀態"]').first().selectOption("IN_STOCK");
await page.fill(".inv-search", "登山");
await page.click('.inv-filters button:has-text("查詢")');
await page.waitForTimeout(1200);
await shot(page, "serialized-filtered", { content: true });

// 明細
await page.locator('.inv-table tbody tr button:has-text("詳細")').first().click();
await page.waitForSelector(".inv-detail", { timeout: 10000 });
await page.waitForTimeout(800);
await shot(page, "serialized-detail", { locator: ".inv-detail" });
await page.click('.inv-detail button:has-text("關閉")');
await page.waitForTimeout(400);

// 改價
await page.locator('.inv-table tbody tr button:has-text("改價")').first().click();
await page.waitForSelector(".inv-price-dialog", { timeout: 10000 });
await page.waitForTimeout(400);
await shot(page, "price-dialog", { locator: ".inv-price-dialog" });
await page.fill('input[aria-label="新售價"]', "0");
await page.click('.inv-price-dialog button:has-text("送出")');
await page.waitForTimeout(500);
const priceErr = await page.textContent(".inv-price-dialog .form-error").catch(() => null);
note(`改價錯誤訊息：${priceErr}`);
await page.fill('input[aria-label="新售價"]', "2100");
await page.click('.inv-price-dialog button:has-text("送出")');
await page.waitForTimeout(1500);
await shot(page, "price-changed", { content: true });

// 補印標籤
await page.locator('.inv-table tbody tr button:has-text("補印標籤")').first().click();
await page.waitForSelector(".inv-reprint-ok", { timeout: 15000 });
await page.waitForTimeout(300);
await shot(page, "reprint-label", { locator: ".inv-table tbody tr:has(.inv-reprint-ok)" });

// ── 久滯庫存 ──
await page.click('.inv-tab:has-text("久滯庫存"), button:has-text("久滯庫存")');
await page.waitForTimeout(1200);
await shot(page, "aging", { content: true });

// ── 一般商品：上架 ──
await page.click('button:has-text("一般商品")');
await page.waitForTimeout(1000);
await shot(page, "catalog-empty", { content: true });
await page.click(".inv-catalog-create summary");
await page.waitForTimeout(400);
await page.fill('input[aria-label="品名"]', "高山瓦斯罐 230g");
await page.fill('input[aria-label="售價"]', "180");
await page.fill('input[aria-label="低庫存提醒點"]', "10");
await shot(page, "catalog-create-form", { locator: ".inv-catalog-create" });
await page.click('button:has-text("上架商品")');
await page.waitForTimeout(2000);
const okMsg = await page.textContent(".inv-catalog-create .form-success").catch(() => null);
note(`上架結果：${okMsg}`);
await shot(page, "catalog-created", { content: true });
const sku = await page
  .locator(".inv-table tbody tr td")
  .first()
  .textContent()
  .catch(() => null);
note(`一般商品 SKU：${sku}`);

// ── 散裝批 ──
await page.click('button:has-text("散裝批")');
await page.waitForTimeout(1200);
await shot(page, "bulk-list", { content: true });
await page.locator('.inv-table tbody tr button:has-text("詳細")').first().click();
await page.waitForSelector(".inv-detail", { timeout: 10000 });
await page.waitForTimeout(800);
await shot(page, "bulk-detail", { locator: ".inv-detail" });

writeFileSync(join(dir, "data.json"), JSON.stringify({ sku: sku?.trim() }, null, 2));
await browser.close();
console.log("✅ 06-inventory 完成");
