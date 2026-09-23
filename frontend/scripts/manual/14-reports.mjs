// 手冊 14：報表——**所有分頁**逐一實測、日期切換、CSV/Excel 匯出（實際下載檔案）。
//
// **分組與分頁清單都從畫面讀，不寫死。** 寫死的那一版標題寫「12 個分頁」，而頁面早已長到 15 個
// ——P1 盤點才發現漏拍三頁。新增分頁不會新增路由，只看路由的盤點看不出來；從畫面讀就不會再有這種落差。
// 截圖依拍攝順序編號：01 分組列、02–17 依分組順序的 16 張報表、18 匯出按鈕、19 日期切換。
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
  "現金對帳": "daily-cash",
  "銷售毛利": "sales-margin",
  "經營洞察": "insights",
  "趨勢": "trends",
  "餐飲內用/外帶": "dine-in",
  "活動成效": "campaign-performance",
  "臨時折扣": "discounts",
  "贈品": "gifts",
  "庫存價值": "inventory-value",
  "寄售應付": "consignment-payables",
  "發票月報": "invoice-register",
  "購物金餘額": "liability",
  "購物金進出": "flows",
  "購物金效益": "effectiveness",
  "購物金對帳": "reconciliation",
};

await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
await page.waitForTimeout(2000);
await shot(page, "tabs", { locator: ".rpt-groups" }).catch(() => {});

// 報表分成 5 組（2026-09-23）：逐組點開，再逐張拍。分組與分頁都從畫面讀。
const groups = page.getByRole("tablist", { name: "報表分類" }).getByRole("tab");
const reports = page.getByRole("tablist", { name: "報表", exact: true }).getByRole("tab");
const groupLabels = (await groups.allInnerTexts()).map((t) => t.trim()).filter(Boolean);
if (groupLabels.length === 0) throw new Error("讀不到報表分組——選擇器可能已改，請勿靜默跳過");
let index = 0;
const seen = [];
for (const [g, group] of groupLabels.entries()) {
  await groups.nth(g).click();
  await page.waitForTimeout(600);
  const labels = (await reports.allInnerTexts()).map((t) => t.trim()).filter(Boolean);
  for (const [r, label] of labels.entries()) {
    index += 1;
    seen.push(`${group}／${label}`);
    const slug = SLUGS[label] ?? `tab-${String(index).padStart(2, "0")}`;
    await reports.nth(r).click();
    await page.waitForTimeout(2200);
    await shot(page, slug, { content: true });
    const body = (await page.textContent(".app-main"))?.replace(/\s+/g, " ").slice(0, 160);
    note(`[${group}／${label}] ${body}`);
  }
}
note(`報表共 ${seen.length} 張：${seen.join("、")}`);

// 匯出（今日營運）
await groups.nth(0).click();
await page.click('[role="tab"]:has-text("今日營運")');
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
