// 手冊 08e：購物金＋LINE Pay 混合付款。本機無 LINE Pay 商店憑證，如實記錄驗證到哪一步。
// 流程：設定啟用 LINE Pay → POS 混合付款 → 手持簽署 → 結帳 → 記錄實際回應 → 還原設定。
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { BASE, SHOTS_ROOT, makeShot, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("08-pos-mixed-linepay");
const shot = makeShot(dir);
const acq = JSON.parse(readFileSync(join(SHOTS_ROOT, "05-acquisition", "data.json"), "utf8"));
const contacts = JSON.parse(readFileSync(join(SHOTS_ROOT, "03-contacts", "data.json"), "utf8"));

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
const apiErrors = [];
page.on("response", async (r) => {
  if (r.url().includes("/api/v1/") && r.status() >= 400 && r.status() !== 404) {
    const body = (await r.text().catch(() => "")).slice(0, 220);
    apiErrors.push(`${r.status()} ${r.url().split("/api/v1")[1]} :: ${body}`);
  }
});
await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "domcontentloaded" });
await kiosk.waitForSelector(".kiosk-standby, .kiosk-cart-shell, .kiosk-task", { timeout: 30000 });

async function cartVersion() {
  const text = (await page.textContent(".pos-kiosk-status")) ?? "";
  return Number(/購物車版本 (\d+)/.exec(text)?.[1] ?? 0);
}
async function waitVersionChange(before, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if ((await cartVersion()) > before) return true;
    await page.waitForTimeout(500);
  }
  return false;
}

// 1) 啟用 LINE Pay
await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await page.locator('.card:has(h2:text("行動支付設定")) input[type="checkbox"]').first().check();
await page.click('button:has-text("儲存行動支付設定")');
await page.waitForTimeout(2500);
note("已啟用 LINE Pay");

// 2) POS 建立購物車
await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
await page.waitForTimeout(3000);
if ((await page.locator('button:has-text("開始下一筆")').count()) > 0) {
  await page.click('button:has-text("開始下一筆")');
  await page.waitForTimeout(2000);
}
const removeButtons = page.locator('.pos-cart button:has-text("移除")');
while ((await removeButtons.count()) > 0) {
  await removeButtons.first().click();
  await page.waitForTimeout(900);
}
await page.waitForTimeout(2000);
await page.fill(".pos-scan-input", acq.lotCode);
await page.keyboard.press("Enter");
await page.waitForTimeout(2500);
if ((await page.locator(".pos-member-selected").count()) === 0) {
  await page.fill(".pos-member-search input", contacts.MAIN.phone);
  await page.click('button:has-text("查詢會員")');
  await page.waitForTimeout(1800);
  await page.locator(`.pos-member-results button:has-text("${contacts.MAIN.name}")`).click();
  await page.waitForTimeout(2500);
}
await shot(page, "tender-modes-with-linepay", { locator: ".pos-tender-modes" });
await page.locator('.pos-tender-mode:has-text("購物金＋其他付款") input').check();
await page.waitForTimeout(1200);
await page.locator('.pos-mixed-method:has-text("LINE Pay") input').check();
await page.waitForTimeout(1000);
let v = await cartVersion();
await page.locator(".pos-mixed-input-row input").fill("200");
await waitVersionChange(v);
await page.waitForTimeout(2500);
await page.fill('input[name="linepay_one_time_key"]', "999999999999999999");
await page.waitForTimeout(1500);
await shot(page, "mixed-linepay-panel", { locator: ".pos-right" });
note(`拆分：${(await page.textContent(".pos-payment-split"))?.replace(/\s+/g, " ")}`);

// 3) 手持簽署
v = await cartVersion();
await waitVersionChange(v, 6000);
await page.waitForTimeout(2000);
await page.click('.pos-sign-panel button:has-text("送至手持裝置簽署")');
await page.waitForTimeout(3000);
await kiosk.waitForSelector(".kiosk-task", { timeout: 30000 });
await kiosk.waitForTimeout(1500);
const canvas = kiosk.locator("canvas.kiosk-sign-canvas");
await canvas.scrollIntoViewIfNeeded();
const box = await canvas.boundingBox();
await kiosk.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.6);
await kiosk.mouse.down();
for (const [fx, fy] of [[0.32, 0.3], [0.45, 0.7], [0.58, 0.35], [0.72, 0.62]]) {
  await kiosk.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 14 });
}
await kiosk.mouse.up();
await kiosk.waitForTimeout(500);
await kiosk.click('button:has-text("確認並送出")');
await kiosk.waitForTimeout(3000);
await page.waitForSelector(".pos-sign-done", { timeout: 30000 });
await page.waitForTimeout(1200);
note("客人已完成購物金扣抵簽署");

// 4) 結帳（預期：LINE Pay 呼叫失敗）
await page.click(".pos-checkout");
await page.waitForTimeout(15000);
const ok = (await page.locator(".pos-complete").count()) > 0;
const notice = await page.textContent(".pos-right .form-error").catch(() => null);
note(`結帳結果：${ok ? "成立" : "未成立"}；畫面訊息＝${notice}`);
await shot(page, "checkout-result", { content: true });
const uncertain = (await page.locator(".pos-sign-panel:has-text('LINE Pay 付款待對帳')").count()) > 0;
note(`是否進入「LINE Pay 付款待對帳」狀態：${uncertain}`);
if (uncertain) await shot(page, "payment-uncertain-panel", { locator: ".pos-right" });
note(`API 4xx/5xx：\n${apiErrors.join("\n") || "（無）"}`);

// 5) 還原設定
await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await page.locator('.card:has(h2:text("行動支付設定")) input[type="checkbox"]').first().uncheck();
await page.click('button:has-text("儲存行動支付設定")');
await page.waitForTimeout(2500);
note("已還原：LINE Pay 停用");

await browser.close();
console.log("✅ 08e 完成");
