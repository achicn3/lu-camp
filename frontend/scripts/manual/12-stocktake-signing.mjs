// 手冊 12：盤點（建立盤點單／輸入實點／確認調整）＋ 簽署紀錄（篩選／查看簽名證據）。
import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("12-stocktake");
const shot = makeShot(dir);
const { browser, page } = await newBrowser();
await login(page);

// ══ 盤點 ══
await page.goto(`${BASE}/stocktake`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await shot(page, "list-empty", { content: true });
await page.click('button:has-text("建立盤點單")');
await page.waitForSelector(".st-detail", { timeout: 15000 });
await page.waitForTimeout(1200);
await shot(page, "draft-detail", { content: true });

// 輸入實點數（故意少 1 件，示範盤虧）。
// **不可寫死數字**：系統現量隨前面章節的銷售/收貨而變，寫死就會出現「實點＝現量、差異 0」
// 的截圖，和手冊圖說「差異 −1、盤虧 1」對不上（2026-08-03 重跑就發生過）。
// 改為讀出該列的系統現量再填 現量−1，讓盤虧永遠成立。
const firstRow = page.locator(".st-detail tbody tr").first();
const systemQty = Number(
  ((await firstRow.locator("td").nth(1).textContent()) ?? "").replace(/[^\d]/g, ""),
);
if (!Number.isInteger(systemQty) || systemQty < 1) {
  throw new Error(`讀不到可用的系統現量（得到 ${systemQty}），無法示範盤虧`);
}
const counted = systemQty - 1;
note(`盤點示範：系統現量 ${systemQty} → 實點 ${counted}（差異 −1）`);
const input = page.locator(".st-count").first();
await input.fill(String(counted));
await page.waitForTimeout(800);
await shot(page, "counted", { locator: ".st-detail" });

await page.click('button:has-text("確認盤點調整")');
await page.waitForSelector('[aria-label="確認盤點"]', { timeout: 10000 });
await page.waitForTimeout(600);
await shot(page, "confirm-dialog", { locator: ".pos-dialog" });
await page.click('.pos-dialog button:has-text("確認調整")');
await page.waitForTimeout(3000);
await shot(page, "confirmed", { content: true });

await page.goto(`${BASE}/stocktake`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await shot(page, "list-after", { content: true });

// 庫存連動
await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await page.click('button:has-text("一般商品")');
await page.waitForTimeout(1500);
await shot(page, "catalog-after-stocktake", { content: true });

// ══ 簽署紀錄 ══
const dir2 = shotsDir("13-signing");
const shot2 = makeShot(dir2);
await page.goto(`${BASE}/signing`, { waitUntil: "networkidle" });
await page.waitForTimeout(1800);
await shot2(page, "list", { content: true });
await shot2(page, "filters", { locator: ".signing-filters" });

// 依狀態篩選
const statusSelect = page.locator("select").first();
await statusSelect.selectOption({ label: "已簽署" });
await page.waitForTimeout(1500);
await shot2(page, "filter-signed", { content: true });

// 依類型篩選
const kindSelect = page.locator("select").nth(1);
await kindSelect.selectOption({ label: "收購切結" });
await page.waitForTimeout(1500);
await shot2(page, "filter-kind", { content: true });

// 查看簽名證據
const viewBtn = page.locator('table tbody tr button').first();
if ((await viewBtn.count()) > 0) {
  await viewBtn.click();
  await page.waitForTimeout(2000);
  await shot2(page, "evidence", { locator: ".pos-dialog" });
}

await browser.close();
console.log("✅ 12/13 完成");
