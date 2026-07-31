// 手冊 09：交易紀錄——清單、退貨（部分/整筆）、作廢、推送簽收、查看簽名。
import { writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { BASE, SHOTS_ROOT, makeShot, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("09-sales");
const shot = makeShot(dir);

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
page.on("pageerror", (e) => console.log(`⚠ JS 錯誤 ${e}`));
await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "domcontentloaded" });
await kiosk.waitForSelector(".kiosk-standby, .kiosk-cart-shell, .kiosk-task", { timeout: 30000 });

await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await shot(page, "list", { content: true });
const rowTexts = await page.locator(".sales-list tbody tr").allTextContents();
note(`交易列數：${rowTexts.length}`);

// ── 查看簽名（購物金扣抵簽署）──
const signBtn = page.locator('button[aria-label^="查看銷售"]').first();
if ((await signBtn.count()) > 0) {
  await signBtn.click();
  await page.waitForSelector(".pos-dialog, .signature-evidence", { timeout: 15000 });
  await page.waitForTimeout(1500);
  await shot(page, "signature-evidence", { locator: ".pos-dialog" });
  await page.locator('.pos-dialog button:has-text("關閉")').first().click();
  await page.waitForTimeout(600);
}

// ── 推送簽收（交易紀錄簽收，客人於顧客螢幕簽名）──
const ackBtn = page.locator('button[aria-label^="推送銷售"]').first();
if ((await ackBtn.count()) > 0) {
  await ackBtn.click();
  await page.waitForTimeout(2500);
  const ackNote = await page.textContent(".hint:below(.page-title)").catch(() => null);
  note(`推送簽收提示：${ackNote}`);
  await shot(page, "push-ack", { content: true });
  await kiosk.waitForSelector(".kiosk-task", { timeout: 30000 });
  await kiosk.waitForTimeout(1200);
  await kiosk.screenshot({ path: join(dir, "04-kiosk-ack-task.png"), fullPage: true });
  console.log("   📸 04-kiosk-ack-task.png");
  const canvas = kiosk.locator("canvas.kiosk-sign-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  await kiosk.mouse.move(box.x + box.width * 0.25, box.y + box.height * 0.6);
  await kiosk.mouse.down();
  for (const [fx, fy] of [[0.4, 0.35], [0.55, 0.7], [0.7, 0.4]]) {
    await kiosk.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 12 });
  }
  await kiosk.mouse.up();
  await kiosk.waitForTimeout(400);
  await kiosk.click('button:has-text("確認並送出")');
  await kiosk.waitForTimeout(2500);
  await kiosk.screenshot({ path: join(dir, "05-kiosk-ack-done.png"), fullPage: true });
  console.log("   📸 05-kiosk-ack-done.png");
}

// ── 退貨（挑一筆現金單，做部分退貨）──
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(1500);
const returnBtn = page.locator('button[aria-label^="退貨銷售"]').last();
await returnBtn.click();
await page.waitForSelector(".return-lines-table", { timeout: 15000 });
await page.waitForTimeout(1000);
await shot(page, "return-dialog", { locator: ".pos-dialog" });
await page.click('button:has-text("整筆退貨")');
await page.waitForTimeout(800);
await page.fill('.pos-dialog input[placeholder^="例：尺寸不合"]', "操作手冊示範：商品瑕疵");
await page.waitForTimeout(400);
await shot(page, "return-filled", { locator: ".pos-dialog" });
const confirmText = await page.textContent('.pos-dialog button.btn-danger');
note(`退貨按鈕文字：${confirmText}`);
await page.click(".pos-dialog button.btn-danger");
await page.waitForTimeout(4000);
await shot(page, "return-done", { content: true });
const noteText = await page.textContent(".form-success").catch(() => null);
note(`退貨結果：${noteText}`);

// ── 作廢（限管理者）──
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(1500);
const voidBtn = page.locator('button[aria-label^="作廢銷售"]').last();
if ((await voidBtn.count()) > 0) {
  await voidBtn.click();
  await page.waitForSelector('[aria-label="作廢銷售確認"]', { timeout: 15000 });
  await page.waitForTimeout(800);
  await shot(page, "void-dialog", { locator: ".pos-dialog" });
  const voidConfirm = page.locator('.pos-dialog button.btn-danger');
  await voidConfirm.click();
  await page.waitForTimeout(4000);
  await shot(page, "void-done", { content: true });
  note(`作廢結果：${await page.textContent(".form-success").catch(() => null)}`);
}

writeFileSync(join(dir, "data.json"), JSON.stringify({ rows: rowTexts.length }, null, 2));
await browser.close();
console.log("✅ 09-sales 完成");
