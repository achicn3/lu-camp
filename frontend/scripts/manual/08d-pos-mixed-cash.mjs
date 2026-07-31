// 手冊 08d：POS「購物金＋現金」混合付款（修復後回歸驗證）。
// 也順帶實測「購物金＋LINE Pay」在無金流憑證環境下會停在哪一步。
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { BASE, makeShot, note, SHOTS_ROOT, shotsDir, statePath } from "./_lib.mjs";

const dir = shotsDir("08-pos-mixed-cash");
const shot = makeShot(dir);
const acq = JSON.parse(readFileSync(join(SHOTS_ROOT, "05-acquisition", "data.json"), "utf8"));
const contacts = JSON.parse(readFileSync(join(SHOTS_ROOT, "03-contacts", "data.json"), "utf8"));

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
const apiErrors = [];
page.on("pageerror", (e) => console.log(`⚠ POS JS 錯誤 ${e}`));
page.on("response", async (r) => {
  if (r.url().includes("/api/v1/") && r.status() >= 400 && r.status() !== 404) {
    const body = (await r.text().catch(() => "")).slice(0, 200);
    apiErrors.push(`${r.status()} ${r.url().split("/api/v1")[1]} :: ${body}`);
    console.log(`   ↩ ${r.status()} ${r.url().split("/api/v1")[1]} :: ${body}`);
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
  if ((await page.locator('button:has-text("開始下一筆")').count()) > 0) {
    await page.click('button:has-text("開始下一筆")');
    await page.waitForTimeout(2000);
  }
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
  await page.waitForTimeout(2000);
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(3000);
}
async function buildCart(remainder) {
  if ((await page.locator(".pos-cart tbody tr").count()) === 0) {
    await page.fill(".pos-scan-input", acq.lotCode);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(2500);
  }
  if ((await page.locator(".pos-member-selected").count()) === 0) {
    await page.fill(".pos-member-search input", contacts.MAIN.phone);
    await page.click('button:has-text("查詢會員")');
    await page.waitForTimeout(1800);
    await page.locator(`.pos-member-results button:has-text("${contacts.MAIN.name}")`).click();
    await page.waitForTimeout(2500);
  }
  await page.locator('.pos-tender-mode:has-text("購物金＋其他付款") input').check();
  await page.waitForTimeout(1200);
  if (remainder !== "CASH") {
    await page.locator(`.pos-mixed-method:has-text("${remainder === "LINE_PAY" ? "LINE Pay" : "台灣Pay"}") input`).check();
    await page.waitForTimeout(1000);
  }
  const v = await cartVersion();
  await page.locator('.pos-mixed-input-row input').fill("200");
  await waitVersionChange(v);
  await page.waitForTimeout(2500);
}

await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await resetCart();

// ══ 情境一：購物金 200 ＋ 現金餘額（修復後應可完成）══
await buildCart("CASH");
await shot(page, "mixed-cash-split", { locator: ".pos-right" });
note(`拆分：${(await page.textContent(".pos-payment-split"))?.replace(/\s+/g, " ")}`);
await page.click('.pos-sign-panel button:has-text("送至手持裝置簽署")');
await page.waitForTimeout(3000);
await signOnKiosk();
await page.waitForSelector(".pos-sign-done", { timeout: 30000 });
await page.waitForTimeout(1200);
await shot(page, "mixed-cash-signed", { locator: ".pos-sign-panel" });
await page.click(".pos-checkout");
await page.waitForTimeout(9000);
const done = (await page.locator(".pos-complete").count()) > 0;
if (done) {
  const text = await page.textContent(".pos-complete");
  note(`✅ 購物金＋現金結帳成立：${text?.replace(/\s+/g, " ").slice(0, 160)}`);
  await shot(page, "mixed-cash-complete", { content: true });
  writeFileSync(join(dir, "data.json"), JSON.stringify({ saleId: /#(\d+)/.exec(text)?.[1] }, null, 2));
  await page.locator('.pos-dialog button:has-text("不用，完成")').click().catch(() => {});
} else {
  note(`❌ 仍未成立：${await page.textContent(".pos-right .form-error").catch(() => "—")}`);
  await page.screenshot({ path: join(dir, "diag-cash.png"), fullPage: true });
}

// ══ 情境二：購物金 ＋ LINE Pay（本機無 LINE Pay 憑證，驗證到能驗的最後一步）══
await resetCart();
if ((await page.locator('.pos-tender-mode:has-text("LINE Pay")').count()) === 0) {
  note("LINE Pay 未於設定啟用，跳過情境二（需先到設定 → 行動支付設定啟用）");
} else {
  await buildCart("LINE_PAY");
  await page.fill('input[name="linepay_one_time_key"]', "000000000000000000");
  await page.waitForTimeout(1500);
  await shot(page, "mixed-linepay-split", { locator: ".pos-right" });
  const v = await cartVersion();
  await waitVersionChange(v, 6000);
  await page.waitForTimeout(2000);
  await page.click('.pos-sign-panel button:has-text("送至手持裝置簽署")');
  await page.waitForTimeout(3000);
  await signOnKiosk();
  await page.waitForSelector(".pos-sign-done", { timeout: 30000 });
  await page.waitForTimeout(1200);
  await page.click(".pos-checkout");
  await page.waitForTimeout(12000);
  const ok = (await page.locator(".pos-complete").count()) > 0;
  const notice = await page.textContent(".pos-right .form-error").catch(() => null);
  note(`購物金＋LINE Pay 結果：${ok ? "成立" : "未成立"}；訊息＝${notice}`);
  await shot(page, "mixed-linepay-result", { content: true });
}

note(`API 4xx/5xx 紀錄：\n${apiErrors.join("\n") || "（無）"}`);
await browser.close();
console.log("✅ 08d 完成");
