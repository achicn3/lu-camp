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
// 低庫存提示是列表頂端一條，有低庫存才出現；展開看明細。
// 截圖依呼叫順序編號，這張不論有沒有低庫存都要截，後面的圖號才不會位移。
const lowStock = page.getByRole("region", { name: "低庫存提醒" });
if (await lowStock.count()) {
  await lowStock.getByRole("button", { name: "查看" }).click();
  await page.waitForTimeout(500);
  await shot(page, "low-stock", { locator: ".pur-lowstock-banner" });
} else {
  await shot(page, "low-stock", { content: true });
}

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
await page.click('.pur-page-head a:has-text("＋ 建立採購單")');
await page.waitForURL(`${BASE}/purchasing/new`);
await page.waitForTimeout(1000);
await shot(page, "po-create-empty", { content: true });

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
await shot(page, "po-product-search", { locator: ".pur-create-page" });
await page.locator(".pur-search-results ul button").first().click();
await page.waitForTimeout(800);
await page.locator(".pur-lines .pur-qty").first().fill("24");
await page.waitForTimeout(300);
await page.locator(".pur-lines .pur-cost").first().fill("120");
await page.waitForTimeout(600);
await shot(page, "po-lines", { locator: ".pur-create-page" });

// 送出後直接進採購單明細頁（取代舊的詳情視窗）
await page.click('button:has-text("送出採購")');
await page.waitForURL(/\/purchasing\/\d+$/, { timeout: 10000 });
await page.waitForTimeout(1500);
await shot(page, "po-detail", { content: true });

// 回列表看狀態篩選
await page.click('a:has-text("← 回採購單列表")');
await page.waitForURL(`${BASE}/purchasing`);
await page.click('.settle-tabs button:has-text("待收貨")');
await page.waitForTimeout(1500);
await shot(page, "po-created", { content: true });
await shot(page, "po-filter-pending", { locator: ".pur-orders" });

// 收貨入庫（含進項發票）：列表的「收貨入庫」會進明細頁並直接打開收貨視窗
await page.locator('.pur-orders tbody tr a:has-text("收貨入庫")').first().click();
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
