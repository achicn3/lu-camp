// 手冊 14：報表——12 個分頁逐一實測、日期切換、CSV/Excel 匯出（實際下載檔案）。
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("14-reports");
const shot = makeShot(dir);
const downloadDir = join(dir, "downloads");
mkdirSync(downloadDir, { recursive: true });
const { browser, page } = await newBrowser({ width: 1440, height: 1000 });
await login(page);

const TABS = [
  ["今日營運", "dashboard"],
  ["經營洞察", "insights"],
  ["趨勢", "trends"],
  ["現金對帳", "daily-cash"],
  ["銷售毛利", "sales-margin"],
  ["活動成效", "campaign-performance"],
  ["庫存價值", "inventory-value"],
  ["寄售應付", "consignment-payables"],
  ["負債", "liability"],
  ["流量", "flows"],
  ["效益指標", "effectiveness"],
  ["對帳", "reconciliation"],
];

await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
await page.waitForTimeout(2000);
await shot(page, "tabs", { locator: ".rpt-tabs, nav, .settle-tabs" }).catch(() => {});

for (const [label, slug] of TABS) {
  await page.click(`button:has-text("${label}")`);
  await page.waitForTimeout(2200);
  await shot(page, slug, { content: true });
  const body = (await page.textContent(".app-main"))?.replace(/\s+/g, " ").slice(0, 160);
  note(`[${label}] ${body}`);
}

// 匯出（今日營運）
await page.click('button:has-text("今日營運")');
await page.waitForTimeout(1800);
for (const fmt of ["CSV", "Excel"]) {
  const dl = page.waitForEvent("download", { timeout: 20000 });
  await page.locator(`button:has-text("${fmt}")`).first().click();
  const file = await dl;
  const target = join(downloadDir, file.suggestedFilename());
  await file.saveAs(target);
  note(`已下載 ${fmt}：${file.suggestedFilename()}（${existsSync(target) ? "存在" : "缺檔"}）`);
}
await shot(page, "export-buttons", { locator: ".rpt-export, .app-main" });

// 日期切換（昨天，應顯示無資料/空報表）
const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
const dateInput = page.locator('input[type="date"]').first();
if ((await dateInput.count()) > 0) {
  await dateInput.fill(yesterday);
  await page.waitForTimeout(2500);
  await shot(page, "date-changed", { content: true });
  note(`切換日期至 ${yesterday}`);
}

note(`下載檔案：${readdirSync(downloadDir).join(", ")}`);
await browser.close();
console.log("✅ 14-reports 完成");
