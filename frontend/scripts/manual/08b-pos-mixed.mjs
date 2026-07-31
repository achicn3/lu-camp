// 手冊 08b：POS 購物金混合付款（手持簽署）。
// 先實測「購物金＋現金」（目前會被後端擋下並回錯誤，截圖存證），再實測「購物金＋台灣Pay」完成結帳。
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { BASE, SHOTS_ROOT, makeShot, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("08-pos-mixed");
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
page.on("pageerror", (e) => console.log(`⚠ POS JS 錯誤 ${e}`));
page.on("response", async (r) => {
  if (r.url().includes("/api/v1/") && r.status() >= 400 && r.status() !== 404) {
    console.log(`   ↩ ${r.status()} ${r.url()} :: ${(await r.text().catch(() => "")).slice(0, 160)}`);
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
async function signOnKiosk() {
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
}
async function resetCart() {
  if ((await page.locator('.pos-sign-panel button:has-text("撤回簽署並修改")').count()) > 0) {
    await page.click('.pos-sign-panel button:has-text("撤回簽署並修改")');
    await page.waitForTimeout(2500);
  }
  const removeButtons = page.locator('.pos-cart button:has-text("移除")');
  while ((await removeButtons.count()) > 0) {
    await removeButtons.first().click();
    await page.waitForTimeout(900);
  }
  if ((await page.locator('button:has-text("取消歸戶")').count()) > 0) {
    await page.click('button:has-text("取消歸戶")');
  }
  await page.waitForTimeout(2500);
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(3000);
}
async function buildCart() {
  if ((await page.locator(".pos-cart tbody tr").count()) === 0) {
    await page.fill(".pos-scan-input", acq.lotCode);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(2000);
  }
  await page.locator(".pos-cart input.pos-qty").first().fill("2");
  await page.waitForTimeout(2000);
  if ((await page.locator(".pos-member-selected").count()) === 0) {
    await page.fill(".pos-member-search input", contacts.MAIN.phone);
    await page.click('button:has-text("查詢會員")');
    await page.waitForTimeout(1800);
    await page.locator(`.pos-member-results button:has-text("${contacts.MAIN.name}")`).click();
    await page.waitForTimeout(2500);
  }
  await page.locator('.pos-tender-mode:has-text("購物金＋其他付款") input').check();
  await page.waitForTimeout(1200);
}

await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
const ONLY = process.env.MANUAL_ONLY ?? "";
let v = 0;
if (ONLY !== "2") {
await resetCart();

// ── 情境一：購物金＋現金（實測：結帳被擋下）──
await buildCart();
await shot(page, "mixed-panel", { locator: ".pos-mixed-panel" });
v = await cartVersion();
await page.click('button:has-text("使用可用上限")');
await waitVersionChange(v);
await page.waitForTimeout(2500);
await shot(page, "mixed-split-cash", { locator: ".pos-right" });
await page.click('.pos-sign-panel button:has-text("送至手持裝置簽署")');
await page.waitForTimeout(3000);
await signOnKiosk();
await page.waitForSelector(".pos-sign-done", { timeout: 30000 });
await page.waitForTimeout(1000);
await shot(page, "sign-done", { locator: ".pos-sign-panel" });
await page.click(".pos-checkout");
await page.waitForTimeout(6000);
const failed = (await page.locator(".pos-complete").count()) === 0;
const failNotice = await page.textContent(".pos-right .form-error").catch(() => null);
note(`購物金＋現金結帳結果：${failed ? "未成立" : "成立"}；訊息＝${failNotice}`);
if (failed) await shot(page, "mixed-cash-blocked", { locator: ".pos-right" });
}

// ── 情境二：購物金＋台灣Pay（可完成）──
await resetCart();
await buildCart();
await page.locator('.pos-mixed-method:has-text("台灣Pay") input').check();
await page.waitForTimeout(1000);
v = await cartVersion();
await page.click('button:has-text("使用可用上限")');
await waitVersionChange(v);
await page.waitForTimeout(2500);
await shot(page, "mixed-split-taiwanpay", { locator: ".pos-right" });
await page.locator(".pos-payment-confirm input").check();
await page.waitForTimeout(1500);
v = await cartVersion();
await waitVersionChange(v, 6000);
await page.waitForTimeout(2000);
await page.click('.pos-sign-panel button:has-text("送至手持裝置簽署")');
await page.waitForTimeout(3000);
await shot(page, "sign-pushed", { locator: ".pos-sign-panel" });
await kiosk.waitForSelector(".kiosk-task", { timeout: 30000 });
await kiosk.waitForTimeout(1500);
await kiosk.screenshot({ path: join(dir, "08-kiosk-credit-task.png"), fullPage: true });
console.log("   📸 08-kiosk-credit-task.png");
await signOnKiosk();
await page.waitForSelector(".pos-sign-done", { timeout: 30000 });
await page.waitForTimeout(1200);
await page.click(".pos-checkout");
await page.waitForTimeout(8000);
const completed = (await page.locator(".pos-complete").count()) > 0;
console.log("購物金＋台灣Pay 完成？", completed);
if (completed) {
  const done = await page.textContent(".pos-complete");
  note(`交易完成：${done?.replace(/\s+/g, " ").slice(0, 200)}`);
  await shot(page, "complete-mixed", { content: true });
  writeFileSync(join(dir, "data.json"), JSON.stringify({ B: /#(\d+)/.exec(done)?.[1] }, null, 2));
} else {
  console.log("notice:", await page.textContent(".pos-right .form-error").catch(() => "—"));
  await page.screenshot({ path: join(dir, "diag-taiwanpay.png"), fullPage: true });
}
await browser.close();
