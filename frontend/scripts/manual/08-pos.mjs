// 手冊 08：POS 結帳——掃碼、餐飲點餐、會員歸戶、活動折扣、現金找零、購物金＋現金混合（手持簽署）、
// 台灣Pay、寄售品售出、列印明細。
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { BASE, makeShot, note, SHOTS_ROOT, shotsDir, statePath } from "./_lib.mjs";

const dir = shotsDir("08-pos");
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
page.on("pageerror", (e) => console.log(`⚠ POS JS 錯誤 ${e}`));
await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "domcontentloaded" });
await kiosk.waitForSelector(".kiosk-standby, .kiosk-cart-shell, .kiosk-task", { timeout: 30000 });

async function scan(code) {
  await page.fill(".pos-scan-input", code);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(1500);
}
const sales = {};

// ══ 交易 A：序號品（活動折扣）＋ 餐飲 ＋ 會員 ＋ 現金 ══
await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await shot(page, "pos-empty", { content: true });
await shot(page, "campaign-banner", { locator: ".pos-campaign-banner" });
await shot(page, "kiosk-status", { locator: ".pos-kiosk-status" });

// 掃到不存在的條碼
await scan("S1-XXXXXXXXXX");
const scanErr = await page.textContent(".pos-scan .form-error").catch(() => null);
note(`掃碼錯誤訊息：${scanErr}`);
await shot(page, "scan-error", { locator: ".pos-scan" });

await scan(acq.code1); // 登山背包
await shot(page, "cart-one-item", { locator: ".pos-left" });

// 餐飲磚
await shot(page, "menu-tiles", { locator: ".pos-menu" });
await page.locator('.pos-menu-tile:has-text("手沖咖啡")').click();
await page.waitForSelector(".pos-qty-dialog", { timeout: 8000 });
await page.waitForTimeout(400);
await page.fill('input[aria-label="數量"]', "2");
await shot(page, "menu-qty-dialog", { locator: ".pos-qty-dialog" });
await page.click('.pos-qty-dialog button:has-text("加入購物車")');
await page.waitForTimeout(1500);
await shot(page, "cart-with-menu", { locator: ".pos-left" });

// 會員歸戶
await page.fill('.pos-member-search input', contacts.MAIN.phone);
await page.click('button:has-text("查詢會員")');
await page.waitForTimeout(1500);
await shot(page, "member-search-results", { locator: ".pos-member" });
await page.locator(`.pos-member-results button:has-text("${contacts.MAIN.name}")`).click();
await page.waitForTimeout(1500);
await shot(page, "member-selected", { locator: ".pos-member" });

// 顧客螢幕同步畫面
await kiosk.waitForSelector(".kiosk-cart-shell", { timeout: 20000 });
await kiosk.waitForTimeout(1200);
await kiosk.screenshot({ path: join(dir, "11b-kiosk-cart.png"), fullPage: true });
console.log("   📸 11b-kiosk-cart.png");

// **有餐飲品項就必須選內用/外帶**（docs/35）：這是本腳本寫成之後才加的規則，
// 不選的話結帳鈕會一直停用——畫面不會說為什麼，只是按不下去。
// 這裡選外帶（內用還要選桌號，那條流程由 08g 專門示範）。
await page.locator('.pos-dinein-mode:has-text("外帶")').click();
await page.waitForTimeout(500);
await shot(page, "dinein-takeout", { locator: ".pos-dinein-panel" });
note("購物車裡有餐飲品項時，一定要先選內用或外帶，否則結帳鈕不會亮");

// 現金收款＋找零
await page.fill(".pos-tender input[inputmode='numeric']", "");
const receivedField = page.locator('.field:has-text("實收現金") input');
await receivedField.fill("2000");
await page.waitForTimeout(600);
const change = await page.textContent(".pos-change").catch(() => null);
note(`找零：${change}`);
await shot(page, "tender-cash", { locator: ".pos-right" });

await page.click(".pos-checkout");
await page.waitForSelector(".pos-complete", { timeout: 25000 });
await page.waitForTimeout(1200);
const doneA = await page.textContent(".pos-complete");
note(`交易 A 完成：${doneA?.replace(/\s+/g, " ").slice(0, 140)}`);
sales.A = /#(\d+)/.exec(doneA)?.[1];
await shot(page, "complete-dialog", { locator: ".pos-dialog" });
await page.click('.pos-dialog button:has-text("列印明細")');
await page.waitForTimeout(2500);
await shot(page, "print-dialog-done", { locator: ".pos-dialog" });
await page.click('.pos-dialog button:has-text("完成")');
await page.waitForTimeout(600);
await shot(page, "complete-screen", { content: true });
await page.click('button:has-text("開始下一筆")');
await page.waitForTimeout(1500);

// ══ 交易 B：散裝 ×2 ＋ 購物金＋現金混合（手持簽署）══
await scan(acq.lotCode);
await page.locator('.pos-cart input.pos-qty').first().fill("2");
await page.waitForTimeout(1500);
await page.fill('.pos-member-search input', contacts.MAIN.phone);
await page.click('button:has-text("查詢會員")');
await page.waitForTimeout(1500);
await page.locator(`.pos-member-results button:has-text("${contacts.MAIN.name}")`).click();
await page.waitForTimeout(1500);

await page.locator('.pos-tender-mode:has-text("購物金＋其他付款") input').check();
await page.waitForTimeout(800);
await shot(page, "mixed-panel", { locator: ".pos-mixed-panel" });
await page.click('button:has-text("使用可用上限")');
await page.waitForTimeout(1200);
await shot(page, "mixed-split", { locator: ".pos-right" });

await page.click('.pos-sign-panel button:has-text("送至手持裝置簽署")');
await page.waitForTimeout(2500);
await shot(page, "sign-pushed", { locator: ".pos-sign-panel" });

await kiosk.waitForSelector(".kiosk-task", { timeout: 30000 });
await kiosk.waitForTimeout(1200);
await kiosk.screenshot({ path: join(dir, "19b-kiosk-credit-task.png"), fullPage: true });
console.log("   📸 19b-kiosk-credit-task.png");
const canvas = kiosk.locator("canvas.kiosk-sign-canvas");
await canvas.scrollIntoViewIfNeeded();
const box = await canvas.boundingBox();
await kiosk.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.6);
await kiosk.mouse.down();
for (const [fx, fy] of [[0.32, 0.3], [0.45, 0.7], [0.58, 0.35], [0.72, 0.62]]) {
  await kiosk.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 14 });
}
await kiosk.mouse.up();
await kiosk.waitForTimeout(400);
await kiosk.click('button:has-text("確認並送出")');
await kiosk.waitForTimeout(2500);

await page.waitForSelector(".pos-sign-done", { timeout: 30000 });
await page.waitForTimeout(800);
await shot(page, "sign-done", { locator: ".pos-sign-panel" });
await page.click(".pos-checkout");
await page.waitForSelector(".pos-complete", { timeout: 30000 });
await page.waitForTimeout(1200);
const doneB = await page.textContent(".pos-complete");
sales.B = /#(\d+)/.exec(doneB)?.[1];
note(`交易 B 完成：${doneB?.replace(/\s+/g, " ").slice(0, 160)}`);
await shot(page, "complete-mixed", { content: true });
await page.locator('.pos-dialog button:has-text("不用，完成")').click().catch(() => {});
await page.waitForTimeout(600);
await page.click('button:has-text("開始下一筆")');
await page.waitForTimeout(1500);

// ══ 交易 C：寄售品 ＋ 台灣Pay ══
await scan(acq.code3);
await page.waitForTimeout(800);
await page.locator('.pos-tender-mode:has-text("台灣Pay") input').check();
await page.waitForTimeout(800);
await shot(page, "tender-taiwanpay", { locator: ".pos-right" });
const beforeCheck = await page.locator(".pos-checkout").isDisabled();
note(`未勾選「已於台灣Pay收到」時，結帳鈕停用＝${beforeCheck}`);
await page.locator(".pos-payment-confirm input").check();
await page.waitForTimeout(600);
await shot(page, "taiwanpay-confirmed", { locator: ".pos-tender" });
await page.click(".pos-checkout");
await page.waitForSelector(".pos-complete", { timeout: 25000 });
await page.waitForTimeout(1200);
const doneC = await page.textContent(".pos-complete");
sales.C = /#(\d+)/.exec(doneC)?.[1];
note(`交易 C（寄售品）完成：${doneC?.replace(/\s+/g, " ").slice(0, 140)}`);
await shot(page, "complete-consignment-sale", { content: true });

writeFileSync(join(dir, "data.json"), JSON.stringify(sales, null, 2));
note(JSON.stringify(sales));
await browser.close();
console.log("✅ 08-pos 完成");
