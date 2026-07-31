// 手冊 17：開啟電子發票／LINE Pay 後的 POS 畫面（本機無 Amego／LINE Pay 憑證，僅驗證到畫面與
// 實際回應訊息，不偽稱開立成功）。跑完會把設定還原。
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { allowEInvoiceIssue, apiJson, apiLogin, BASE, makeShot, note, SHOTS_ROOT, shotsDir, statePath, withSettings } from "./_lib.mjs";

const dir = shotsDir("17-einvoice");
const shot = makeShot(dir);
const acq = JSON.parse(readFileSync(join(SHOTS_ROOT, "05-acquisition", "data.json"), "utf8"));

const browser = await chromium.launch();
const staffCtx = await browser.newContext({
  storageState: statePath("staff-state.json"),
  viewport: { width: 1440, height: 1000 },
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

// 本腳本會開啟發票開關並結帳（＝實際送出開立請求）→ 需明確 opt-in（見 allowEInvoiceIssue）。
const settingsNow = (await apiJson(await apiLogin(), "GET", "/api/v1/settings")).json;
if (settingsNow === null) throw new Error("讀不到目前設定，中止");
if (settingsNow.einvoice_enabled) {
  throw new Error("電子發票原本即為啟用狀態：疑似正式環境，中止以免消耗字軌／開出真發票。");
}
if (!allowEInvoiceIssue("開啟電子發票並結帳（會觸發開立）")) {
  await browser.close();
  process.exit(0);
}

await withSettings(["einvoice_enabled", "linepay_enabled"], async () => {

// 1) 於設定開啟電子發票與 LINE Pay
await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
await page.waitForTimeout(2000);
await page.locator('input[name="einvoice_enabled"]').check();
await page.click('button:has-text("儲存一般設定")');
await page.waitForTimeout(2000);
await shot(page, "settings-einvoice-on", { locator: '.card:has(h2:text("一般設定"))' });

await page.locator('.card:has(h2:text("行動支付設定")) input[type="checkbox"]').first().check();
await page.click('button:has-text("儲存行動支付設定")');
await page.waitForTimeout(2000);
await shot(page, "settings-linepay-on", { locator: '.card:has(h2:text("行動支付設定"))' });

// 2) POS：發票欄位與 LINE Pay 選項
await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
await page.waitForTimeout(3000);
if ((await page.locator('button:has-text("開始下一筆")').count()) > 0) {
  await page.click('button:has-text("開始下一筆")');
  await page.waitForTimeout(2000);
}
await page.fill(".pos-scan-input", acq.lotCode);
await page.keyboard.press("Enter");
await page.waitForTimeout(2500);
await shot(page, "pos-invoice-fields", { locator: ".pos-invoice" });
await shot(page, "pos-tender-linepay", { locator: ".pos-tender-modes" });

// 3) 發票欄位驗證訊息
await page.fill('input[name="inv-tax-id"]', "1234");
await page.waitForTimeout(600);
note(`統編格式錯誤：${await page.textContent(".pos-invoice .form-error").catch(() => null)}`);
await shot(page, "invoice-taxid-error", { locator: ".pos-invoice" });
await page.fill('input[name="inv-tax-id"]', "12345675");
await page.fill('input[name="inv-buyer-name"]', "手冊測試股份有限公司");
await page.waitForTimeout(600);
await shot(page, "invoice-b2b", { locator: ".pos-invoice" });

// 4) 結帳（現金）→ 觀察發票開立實際結果
await page.click(".pos-checkout");
await page.waitForSelector(".pos-complete", { timeout: 30000 });
await page.waitForTimeout(6000);
const invoiceNote = await page.textContent(".pos-invoice-note").catch(() => null);
note(`發票開立結果：${invoiceNote}`);
await shot(page, "checkout-invoice-result", { content: true });
await page.locator('.pos-dialog button:has-text("不用，完成")').click().catch(() => {});

});

await browser.close();
console.log("✅ 17 完成");
