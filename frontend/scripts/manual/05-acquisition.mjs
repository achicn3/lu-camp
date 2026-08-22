// 手冊 05：收購鑑價入庫——買斷（現金）、買斷（購物金＋手持簽署）、寄售、散裝批、作廢收購。
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { BASE, makeShot, note, SHOTS_ROOT, shotsDir, statePath } from "./_lib.mjs";

const dir = shotsDir("05-acquisition");
const shot = makeShot(dir);
const contacts = JSON.parse(readFileSync(join(SHOTS_ROOT, "03-contacts", "data.json"), "utf8"));
const RUN = String(Date.now()).slice(-5);

const browser = await chromium.launch();
const staffCtx = await browser.newContext({
  storageState: statePath("staff-state.json"),
  viewport: { width: 1440, height: 900 },
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
page.on("pageerror", (e) => console.log(`⚠ 店員頁 JS 錯誤 ${e}`));
await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "domcontentloaded" });
await kiosk.waitForSelector(".kiosk-standby, .kiosk-task", { timeout: 30000 });
await kiosk.waitForTimeout(1000);

async function comboCreate(label, value) {
  const input = page.getByLabel(label, { exact: true });
  await input.click();
  await input.fill(value);
  await page.waitForTimeout(600);
  const create = page.locator(".combo-menu .combo-create").filter({ hasText: value });
  await create.waitFor({ state: "visible", timeout: 10000 });
  await create.click();
  await page.waitForTimeout(400);
}
async function comboPick(label, typed, optionText) {
  const input = page.getByLabel(label, { exact: true });
  await input.click();
  await input.fill(typed);
  await page.waitForTimeout(600);
  await page.locator(".combo-menu .combo-option").filter({ hasText: optionText }).first().click();
  await page.waitForTimeout(300);
}
async function pickSeller(name) {
  await page.fill('input[aria-label="賣方搜尋"]', name);
  await page.waitForTimeout(900);
  await page.locator(`.acq-results button:has-text("${name}")`).first().click();
  await page.waitForTimeout(400);
}

const created = {};

// ══ 一、買斷（現金撥款，不經手持）══
await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await shot(page, "page-buyout-empty", { content: true });
await shot(page, "type-tabs", { locator: ".acq-types" });

// 賣方搜尋
await page.fill('input[aria-label="賣方搜尋"]', "手冊測試客");
await page.waitForTimeout(1000);
await shot(page, "seller-search", { locator: ".card:has(h2:text('賣方'))" });
await page.locator(`.acq-results button:has-text("${contacts.MAIN.name}")`).first().click();
await page.waitForTimeout(500);
await shot(page, "seller-selected", { locator: ".acq-seller" });

// 鑑價列
await page.fill('input[aria-label="品名"]', "登山背包 60L");
await comboCreate("品牌", `手冊品牌${RUN}`);
await comboCreate("型號", `HB-${RUN}`);
await comboCreate("分類", `露營裝備${RUN}`);
await page.locator(".acq-row select").first().selectOption("A");
await page.fill('input[aria-label="估計轉售價"]', "3000");
await page.waitForTimeout(1200);
const aid = await page.textContent(".acq-aid").catch(() => null);
note(`定價輔助：${aid}`);
await shot(page, "row-pricing-aid", { locator: ".acq-row", highlight: [".acq-aid"] });

await page.fill('input[aria-label="收購價"]', "1200");
await page.waitForTimeout(600);
await shot(page, "row-listed-buttons", { locator: '.field:has(input[aria-label="上架售價（含稅）"])' });
await page.click('button:has-text("套用建議（目標毛利")');
await page.waitForTimeout(400);
const listed = await page.inputValue('input[aria-label="上架售價（含稅）"]');
note(`套用建議售價 → ${listed}`);
await shot(page, "row-filled", { locator: ".acq-row" });

// 超過建議最高收購成本的提醒
await page.fill('input[aria-label="收購價"]', "2900");
await page.waitForTimeout(600);
const warn = await page.textContent(".acq-warn").catch(() => null);
note(`超額警示：${warn}`);
await shot(page, "row-overcost-warning", { locator: '.field:has(input[aria-label="收購價"])' });
await page.fill('input[aria-label="收購價"]', "1200");
await page.waitForTimeout(500);

// 撥款區
await shot(page, "payout-cash", { locator: ".acq-payout" });

// 送出
await page.click('button:has-text("送出收購")');
await page.waitForSelector(".acq-result", { timeout: 20000 });
await page.waitForTimeout(800);
const doneText = await page.textContent(".acq-result");
note(`買斷完成：${doneText?.replace(/\s+/g, " ").slice(0, 120)}`);
created.buyoutCash = /單號 #(\d+)/.exec(doneText)?.[1];
created.code1 = /序號條碼：([SL]\d+-[0-9A-F]+)/.exec(doneText)?.[1];
await shot(page, "result-buyout", { locator: ".acq-result" });

// 列印標籤（真的會從 Brother QL-810W 吐一張紙）
//
// **不要改用假印表機來繞過標籤機沒開**：fake 會讓畫面照樣顯示「已送出列印」，
// 截圖跟真印一模一樣，於是手冊會宣稱「本輪實機印過」而其實沒有——
// docs/37 §5 列為最難察覺的失敗樣態。要跳過就明確跳過，並保留上一輪的真截圖。
if (process.env.MANUAL_SKIP_LABEL_PRINT === "1") {
  note("⚠ 已跳過標籤列印（MANUAL_SKIP_LABEL_PRINT=1）：labels-printed 沿用上一輪的實機截圖");
} else {
  await page.click('button:has-text("列印標籤")');
  await page.waitForSelector(".acq-print-labels .form-success", { timeout: 15000 });
  await page.waitForTimeout(400);
  await shot(page, "labels-printed", { locator: ".acq-print-labels" });
}

// ══ 二、買斷（購物金撥款 ＋ 手持簽署）══
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await pickSeller(contacts.MAIN.name);
await page.fill('input[aria-label="品名"]', "登山帳篷 2人");
await comboPick("品牌", "手冊品牌", `手冊品牌${RUN}`);
await comboPick("分類", "露營裝備", `露營裝備${RUN}`);
await page.locator(".acq-row select").first().selectOption("B");
await page.fill('input[aria-label="收購價"]', "2000");
await page.fill('input[aria-label="上架售價（含稅）"]', "4000");
await page.locator('.acq-payout-mode:has-text("購物金") input').check();
await page.waitForTimeout(600);
const premium = await page.textContent(".acq-premium").catch(() => null);
note(`購物金溢價提示：${premium}`);
await shot(page, "payout-store-credit", { locator: ".acq-payout" });

// 送至手持裝置簽署
await page.click('button:has-text("送至手持裝置簽署")');
await page.waitForSelector(".acq-sign-wait", { timeout: 20000 });
await page.waitForTimeout(600);
await shot(page, "sign-waiting", { locator: ".acq-sign" });

// 顧客螢幕：切結內容 → 同意 → 選購物金 → 簽名 → 送出
await kiosk.waitForSelector(".kiosk-task", { timeout: 30000 });
await kiosk.waitForTimeout(1200);
await shot(kiosk, "kiosk-affidavit-top", { locator: ".kiosk-task-header" });
await kiosk.screenshot({ path: join(dir, "18-kiosk-affidavit-full.png"), fullPage: true });
console.log("   📸 18-kiosk-affidavit-full.png");
await kiosk.locator(".kiosk-agree-check input").check();
await kiosk.locator('.kiosk-payout-options button:has-text("購物金")').click();
await kiosk.waitForTimeout(400);
await shot(kiosk, "kiosk-payout-choice", { locator: ".kiosk-payout" });

const canvas = kiosk.locator("canvas.kiosk-sign-canvas");
await canvas.scrollIntoViewIfNeeded();
const box = await canvas.boundingBox();
await kiosk.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.6);
await kiosk.mouse.down();
for (const [fx, fy] of [[0.3, 0.3], [0.42, 0.72], [0.55, 0.32], [0.68, 0.66], [0.8, 0.4]]) {
  await kiosk.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 14 });
}
await kiosk.mouse.up();
await kiosk.waitForTimeout(400);
await shot(kiosk, "kiosk-signature-drawn", { locator: ".kiosk-signature" });
await kiosk.click('button:has-text("確認並送出")');
await kiosk.waitForTimeout(2500);
await shot(kiosk, "kiosk-signed-thanks", { locator: ".kiosk-thanks, .kiosk-standby" });

await page.waitForSelector(".form-success:has-text('客人已完成簽署')", { timeout: 30000 });
await page.waitForTimeout(600);
await shot(page, "sign-done", { locator: ".acq-sign" });
await shot(page, "payout-locked-by-signature", { locator: ".acq-payout" });

await page.click('button:has-text("送出收購")');
await page.waitForSelector(".acq-result", { timeout: 20000 });
await page.waitForTimeout(800);
const done2 = await page.textContent(".acq-result");
note(`購物金買斷完成：${done2?.replace(/\s+/g, " ").slice(0, 160)}`);
created.buyoutCredit = /單號 #(\d+)/.exec(done2)?.[1];
created.code2 = /序號條碼：([SL]\d+-[0-9A-F]+)/.exec(done2)?.[1];
await shot(page, "result-buyout-credit", { locator: ".acq-result" });

// 憑證聯列印
await page.click('button:has-text("列印收購憑證聯")');
await page.waitForTimeout(2500);
const receiptNote = await page.textContent(".acq-receipt-print .hint").catch(() => null);
note(`憑證聯：${receiptNote}`);
await shot(page, "receipt-printed", { locator: ".acq-receipt-print" });

// ══ 三、寄售 ══
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await page.click('.acq-types button:has-text("寄售")');
await page.waitForTimeout(600);
await pickSeller(contacts.MAIN.name);
await page.fill('input[aria-label="品名"]', "露營桌 蛋捲桌");
await comboPick("分類", "露營裝備", `露營裝備${RUN}`);
await page.locator(".acq-row select").first().selectOption("A");
await page.fill('input[aria-label="上架售價（含稅）"]', "1800");
await page.waitForTimeout(400);
await shot(page, "consignment-form", { content: true });
await page.click('button:has-text("送出收購")');
await page.waitForSelector(".acq-result", { timeout: 20000 });
await page.waitForTimeout(800);
const done3 = await page.textContent(".acq-result");
created.consignment = /單號 #(\d+)/.exec(done3)?.[1];
created.code3 = /序號條碼：([SL]\d+-[0-9A-F]+)/.exec(done3)?.[1];
note(`寄售完成：${done3?.replace(/\s+/g, " ").slice(0, 120)}`);
await shot(page, "consignment-result", { locator: ".acq-result" });

// ══ 四、散裝批 ══
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await page.click('.acq-types button:has-text("散裝")');
await page.waitForTimeout(600);
await pickSeller(contacts.MAIN.name);
await page.fill('.acq-row .field:has-text("名稱") input', "二手營燈雜項");
await comboPick("分類（選填）", "露營裝備", `露營裝備${RUN}`);
await page.fill('.acq-row .field:has-text("整堆收購成本") input', "3000");
await page.locator('.acq-row .field:has-text("收購基準") select').selectOption("BAG");
await page.fill('.acq-row .field:has-text("件數") input', "20");
await page.fill('.acq-row .field:has-text("每件均一價") input', "300");
await page.fill('.acq-row .field:has-text("命名（選填）") input', "手冊測試批");
await page.waitForTimeout(400);
await shot(page, "bulk-form", { content: true });
await page.click('button:has-text("送出收購")');
await page.waitForSelector(".acq-result", { timeout: 20000 });
await page.waitForTimeout(800);
const done4 = await page.textContent(".acq-result");
created.bulk = /單號 #(\d+)/.exec(done4)?.[1];
created.lotCode = /散裝批號：([SL]\d+-[0-9A-F]+)/.exec(done4)?.[1];
note(`散裝完成：${done4?.replace(/\s+/g, " ").slice(0, 120)}`);
await shot(page, "bulk-result", { locator: ".acq-result" });

// ══ 五、作廢收購（管理者）══
// 另建一筆可作廢的買斷，示範查詢 → 作廢
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await pickSeller(contacts.MAIN.name);
await page.fill('input[aria-label="品名"]', "誤登的營柱");
await comboPick("分類", "露營裝備", `露營裝備${RUN}`);
await page.locator(".acq-row select").first().selectOption("C");
await page.fill('input[aria-label="收購價"]', "300");
await page.fill('input[aria-label="上架售價（含稅）"]', "800");
await page.click('button:has-text("送出收購")');
await page.waitForSelector(".acq-result", { timeout: 20000 });
await page.waitForTimeout(600);
const done5 = await page.textContent(".acq-result");
created.toVoid = /單號 #(\d+)/.exec(done5)?.[1];
note(`待作廢單號 #${created.toVoid}`);

await page.locator('input[aria-label="收購單號"]').fill(created.toVoid);
await page.click('.acq-void-lookup button:has-text("查詢")');
await page.waitForSelector(".acq-void-summary", { timeout: 15000 });
await page.waitForTimeout(600);
await shot(page, "void-lookup", { locator: ".acq-void-section" });
await page.click('.acq-void-summary button:has-text("作廢收購")');
await page.waitForSelector(".acq-void-dialog", { timeout: 10000 });
await page.waitForTimeout(400);
await page.fill('textarea[aria-label="作廢原因"]', "操作手冊示範：鑑價輸入錯誤");
await shot(page, "void-dialog", { locator: ".acq-void-dialog" });
await page.click('button:has-text("確認作廢")');
await page.waitForSelector(".acq-void-result", { timeout: 15000 });
await page.waitForTimeout(600);
await shot(page, "void-result", { locator: ".acq-void-result" });

writeFileSync(join(dir, "data.json"), JSON.stringify(created, null, 2));
note(JSON.stringify(created));
await browser.close();
console.log("✅ 05-acquisition 完成");
