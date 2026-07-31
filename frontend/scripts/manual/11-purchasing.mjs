// 手冊 11：採購/補貨——供應商（新增/編輯/停用/啟用/搜尋）、採購單（建立/存草稿/送出/收貨入庫/
// 補登進項發票/取消/詳細/篩選）、低庫存提醒。
import { writeFileSync } from "node:fs";
import { join } from "node:path";

import { BASE, login, makeShot, newBrowser, shotsDir } from "./_lib.mjs";

const dir = shotsDir("11-purchasing");
const shot = makeShot(dir);
const { browser, page } = await newBrowser();
await login(page);

await page.goto(`${BASE}/purchasing`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await shot(page, "po-tab-empty", { content: true });
await shot(page, "low-stock", { locator: '.card:has(h2:text("低庫存提醒"))' });

// ── 供應商 ──
await page.click('.settle-tabs button:has-text("供應商")');
await page.waitForTimeout(1200);
await shot(page, "supplier-tab", { content: true });
await page.fill('input[aria-label="供應商名稱"]', "手冊測試戶外用品行");
await page.fill('input[aria-label="聯絡方式"]', "02-1234-5678");
await page.fill('input[aria-label="統一編號"]', "12345675");
await shot(page, "supplier-create-form", { locator: '.card:has(h2:text("新增供應商"))' });
await page.click('.card:has(h2:text("新增供應商")) button:has-text("新增供應商")');
await page.waitForTimeout(2000);
await shot(page, "supplier-created", { locator: '.card:has(h2:text("供應商清單"))' });

// 編輯供應商
await page.locator('.card:has(h2:text("供應商清單")) tbody tr button:has-text("編輯")').first().click();
await page.waitForSelector('[aria-label="編輯供應商"]', { timeout: 10000 });
await page.waitForTimeout(500);
await page.fill('input[aria-label="編輯聯絡方式"]', "02-1234-5678 / 王經理");
await shot(page, "supplier-edit", { locator: ".pos-dialog" });
await page.locator('.pos-dialog button.btn-primary').first().click();
await page.waitForTimeout(1800);

// 搜尋供應商
await page.fill('input[aria-label="供應商搜尋"]', "手冊測試");
await page.locator('.card:has(h2:text("供應商清單")) button:has-text("搜尋")').click();
await page.waitForTimeout(1500);
await shot(page, "supplier-search", { locator: '.card:has(h2:text("供應商清單"))' });

// ── 採購單 ──
await page.click('.settle-tabs button:has-text("採購單")');
await page.waitForTimeout(1200);
await page.click('button:has-text("＋ 建立採購單")');
await page.waitForTimeout(1000);
await shot(page, "po-create-empty", { locator: '.card:has(h2:text("建立採購單"))' });

// 供應商 combobox
const supplierInput = page.getByLabel("供應商", { exact: true });
await supplierInput.click();
await supplierInput.fill("手冊測試");
await page.waitForTimeout(900);
await page.locator(".combo-menu .combo-option").first().click();
await page.waitForTimeout(600);

// 加入商品
await page.fill('input[aria-label="搜尋一般商品"]', "瓦斯");
await page.waitForTimeout(1200);
await shot(page, "po-product-search", { locator: '.card:has(h2:text("建立採購單"))' });
await page.locator('.pur-product-results button, .card:has(h2:text("建立採購單")) ul button').first().click();
await page.waitForTimeout(800);
await page.locator('.pur-line-table input, table input').first().fill("24");
await page.waitForTimeout(300);
const costInput = page.locator('.card:has(h2:text("建立採購單")) table input').nth(1);
await costInput.fill("120");
await page.waitForTimeout(600);
await shot(page, "po-lines", { locator: '.card:has(h2:text("建立採購單"))' });

await page.click('button:has-text("送出採購")');
await page.waitForTimeout(2500);
await shot(page, "po-created", { content: true });

// 狀態篩選
await page.click('.settle-tabs button:has-text("待收貨")');
await page.waitForTimeout(1500);
await shot(page, "po-filter-pending", { locator: ".pur-po-list, .card" });

// 詳細
await page.locator('tbody tr button:has-text("詳細")').first().click();
await page.waitForSelector('[aria-label="採購單詳情"]', { timeout: 10000 });
await page.waitForTimeout(800);
await shot(page, "po-detail", { locator: ".pos-dialog" });
await page.locator('.pos-dialog button:has-text("關閉")').first().click();
await page.waitForTimeout(600);

// 收貨入庫（含進項發票）
await page.locator('tbody tr button:has-text("收貨入庫")').first().click();
await page.waitForSelector('[aria-label="確認收貨"]', { timeout: 10000 });
await page.waitForTimeout(800);
await shot(page, "po-receive-dialog", { locator: ".pos-dialog" });
await page.fill('input[aria-label="發票號碼"]', "AB12345678");
const today = new Date().toISOString().slice(0, 10);
await page.fill('input[aria-label="發票日期"]', today);
await page.fill('input[aria-label="發票含稅金額"]', "2880");
await shot(page, "po-receive-invoice", { locator: ".pos-dialog" });
await page.locator('.pos-dialog button.btn-primary').first().click();
await page.waitForTimeout(3000);
await shot(page, "po-received", { content: true });

// 庫存已補貨（連動）
await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await page.click('button:has-text("一般商品")');
await page.waitForTimeout(1500);
await shot(page, "catalog-after-receive", { content: true });

writeFileSync(join(dir, "data.json"), JSON.stringify({ ok: true }, null, 2));
await browser.close();
console.log("✅ 11-purchasing 完成");
