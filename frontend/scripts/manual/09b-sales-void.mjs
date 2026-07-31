// 手冊 09b：建立一筆測試交易後，示範「作廢」成功流程（限管理者）。
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { BASE, makeShot, note, SHOTS_ROOT, shotsDir, statePath } from "./_lib.mjs";

const dir = shotsDir("09-sales-void");
const shot = makeShot(dir);
const acq = JSON.parse(readFileSync(join(SHOTS_ROOT, "05-acquisition", "data.json"), "utf8"));

const browser = await chromium.launch();
const staffCtx = await browser.newContext({
  storageState: statePath("staff-state.json"),
  viewport: { width: 1440, height: 950 },
  deviceScaleFactor: 2,
  locale: "zh-TW",
  timezoneId: "Asia/Taipei",
});
const kioskCtx = await browser.newContext({
  storageState: statePath("kiosk-state.json"),
  viewport: { width: 834, height: 1112 },
  deviceScaleFactor: 2,
  locale: "zh-TW",
  timezoneId: "Asia/Taipei",
});
const page = await staffCtx.newPage();
const kiosk = await kioskCtx.newPage();
await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "domcontentloaded" });
await kiosk.waitForSelector(".kiosk-standby, .kiosk-cart-shell, .kiosk-task", { timeout: 30000 });

// 建立一筆散裝現金交易
await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
if ((await page.locator('button:has-text("開始下一筆")').count()) > 0) {
  await page.click('button:has-text("開始下一筆")');
  await page.waitForTimeout(2000);
}
await page.fill(".pos-scan-input", acq.lotCode);
await page.keyboard.press("Enter");
await page.waitForTimeout(2500);
await page.click(".pos-checkout");
await page.waitForSelector(".pos-complete", { timeout: 30000 });
await page.waitForTimeout(1500);
const done = await page.textContent(".pos-complete");
const saleId = /#(\d+)/.exec(done)?.[1];
note(`測試交易 #${saleId} 已建立`);
await page.locator('.pos-dialog button:has-text("不用，完成")').click().catch(() => {});

// 作廢
await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await page.locator(`button[aria-label="作廢銷售 ${saleId}"]`).click();
await page.waitForSelector('[aria-label="作廢銷售確認"]', { timeout: 15000 });
await page.waitForTimeout(800);
await shot(page, "void-dialog", { locator: ".pos-dialog" });
await page.locator(".pos-dialog button.btn-danger").click();
await page.waitForTimeout(4000);
note(`作廢結果：${await page.textContent(".form-success").catch(() => null)}`);
await shot(page, "void-done", { content: true });

await browser.close();
console.log("✅ 09b 完成");
