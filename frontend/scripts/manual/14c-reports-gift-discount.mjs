// 手冊 14c：折扣與贈品報表，以及設定頁的贈品／折扣原因管理（docs/32 §6）。
import { existsSync } from "node:fs";

import { chromium } from "playwright";

import { BASE, login, makeShot, note, shotsDir, statePath } from "./_lib.mjs";

const dir = shotsDir("14-reports-gift-discount");
const shot = makeShot(dir);

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
page.on("pageerror", (e) => console.log(`⚠ 報表 JS 錯誤 ${e}`));
if (!hasState) await login(page);

await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
await page.waitForTimeout(2000);

await page.locator('button:has-text("臨時折扣")').click();
await page.waitForSelector("text=折扣總額", { timeout: 20000 });
await page.waitForTimeout(1500);
await shot(page, "discounts", { fullPage: true });

await page.locator('button:has-text("贈品")').first().click();
await page.waitForSelector("text=原價價值", { timeout: 20000 });
await page.waitForTimeout(1500);
await shot(page, "gifts", { fullPage: true });

await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
await page.waitForSelector("text=贈品原因", { timeout: 20000 });
await page.waitForTimeout(1500);
await shot(page, "reason-cards", {
  locator: '.card:has-text("贈品原因")',
});

await browser.close();
note("14c 完成");
