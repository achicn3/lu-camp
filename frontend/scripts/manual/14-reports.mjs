// 手冊 14：報表——**所有分頁**逐一實測、日期切換、CSV/Excel 匯出（實際下載檔案）。
//
// **分頁清單從畫面讀，不寫死。** 寫死的那一版標題寫「12 個分頁」，而頁面早已長到 15 個
// （新增了「餐飲內用/外帶」「效益指標」「對帳」）——P1 盤點才發現漏拍三頁。
// 新增分頁不會新增路由，只看路由的盤點看不出來；從畫面讀就不會再有這種落差。
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("14-reports");
const shot = makeShot(dir);
const downloadDir = join(dir, "downloads");
mkdirSync(downloadDir, { recursive: true });
const { browser, page } = await newBrowser({ width: 1440, height: 1000 });
await login(page);

// 截圖檔名用的英文 slug；沒對到的分頁一律以序號命名，**不會因此漏拍**。
const SLUGS = {
  "今日營運": "dashboard",
  "經營洞察": "insights",
  "趨勢": "trends",
  "餐飲內用/外帶": "dine-in",
  "現金對帳": "daily-cash",
  "銷售毛利": "sales-margin",
  "臨時折扣": "discounts",
  "贈品": "gifts",
  "活動成效": "campaign-performance",
  "庫存價值": "inventory-value",
  "寄售應付": "consignment-payables",
  "負債": "liability",
  "流量": "flows",
  "效益指標": "effectiveness",
  "對帳": "reconciliation",
};

await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
await page.waitForTimeout(2000);
await shot(page, "tabs", { locator: '[role="tablist"]' }).catch(() => {});

const labels = await page.locator('[role="tablist"] [role="tab"]').allInnerTexts();
const tabs = labels.map((t) => t.trim()).filter(Boolean);
if (tabs.length === 0) throw new Error("讀不到報表分頁清單——選擇器可能已改，請勿靜默跳過");
note(`報表分頁共 ${tabs.length} 個：${tabs.join("、")}`);

for (const [index, label] of tabs.entries()) {
  const slug = SLUGS[label] ?? `tab-${String(index + 1).padStart(2, "0")}`;
  await page.locator('[role="tablist"] [role="tab"]').nth(index).click();
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
