// 手冊 08c：POS 售出寄售品（現金收款＋找零輔助）→ 產生寄售結算（待付款）。
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { BASE, SHOTS_ROOT, makeShot, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("08-pos-consignment");
const shot = makeShot(dir);
const acq = JSON.parse(readFileSync(join(SHOTS_ROOT, "05-acquisition", "data.json"), "utf8"));

const browser = await chromium.launch();
const staffCtx = await browser.newContext({
  storageState: join(SHOTS_ROOT, "staff-state.json"),
  viewport: { width: 1440, height: 950 },
  deviceScaleFactor: 2,
  locale: "zh-TW",
  timezoneId: "Asia/Taipei",
});
const kioskCtx = await browser.newContext({
  storageState: join(SHOTS_ROOT, "kiosk-state.json"),
  viewport: { width: 834, height: 1112 },
  deviceScaleFactor: 2,
  locale: "zh-TW",
  timezoneId: "Asia/Taipei",
});
const page = await staffCtx.newPage();
const kiosk = await kioskCtx.newPage();
await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "domcontentloaded" });
await kiosk.waitForSelector(".kiosk-standby, .kiosk-cart-shell, .kiosk-task", { timeout: 30000 });

await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
if ((await page.locator('button:has-text("開始下一筆")').count()) > 0) {
  await page.click('button:has-text("開始下一筆")');
  await page.waitForTimeout(2000);
}
const removeButtons = page.locator('.pos-cart button:has-text("移除")');
while ((await removeButtons.count()) > 0) {
  await removeButtons.first().click();
  await page.waitForTimeout(900);
}
if ((await page.locator('button:has-text("取消歸戶")').count()) > 0) {
  await page.click('button:has-text("取消歸戶")');
  await page.waitForTimeout(1500);
}
await page.waitForTimeout(2000);

await page.fill(".pos-scan-input", acq.code3); // 寄售的露營桌
await page.keyboard.press("Enter");
await page.waitForTimeout(2500);
await shot(page, "cart-consignment-item", { locator: ".pos-left" });

await page.locator('.field:has-text("實收現金") input').fill("2000");
await page.waitForTimeout(800);
const change = await page.textContent(".pos-change").catch(() => null);
note(`找零顯示：${change}`);
await shot(page, "cash-change", { locator: ".pos-tender" });
await shot(page, "invoice-off-hint", { locator: ".pos-invoice-off" });

await page.click(".pos-checkout");
await page.waitForSelector(".pos-complete", { timeout: 30000 });
await page.waitForTimeout(1500);
const done = await page.textContent(".pos-complete");
note(`寄售品售出完成：${done?.replace(/\s+/g, " ").slice(0, 160)}`);
await shot(page, "complete", { content: true });
await page.locator('.pos-dialog button:has-text("不用，完成")').click().catch(() => {});
writeFileSync(join(dir, "data.json"), JSON.stringify({ C: /#(\d+)/.exec(done)?.[1] }, null, 2));

await browser.close();
console.log("✅ 08c 完成");
