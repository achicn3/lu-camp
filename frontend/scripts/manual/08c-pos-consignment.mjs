// 手冊 08c：POS 售出寄售品（現金收款＋找零輔助）→ 產生寄售結算（待付款）。
import { writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { apiJson, apiLogin, BASE, makeShot, note, shotsDir, statePath } from "./_lib.mjs";

const dir = shotsDir("08-pos-consignment");
const shot = makeShot(dir);

// 05-acquisition 建的那件寄售品，會被 08-pos 的交易 C 先賣掉（台灣Pay），
// 到這裡已是 SOLD、掃了會顯示「非在庫（不可售）」。本節需要一件在庫的寄售品，
// 因此比照 09d 的做法自行以收購 API 補一件（冪等鍵固定，重跑不會長出第二件）。
//
// **必須是這個品名與售價**：手冊圖說寫死了「實收 2,000、應付 1,800 → 找零 $200」，
// 隨手撿一件在庫寄售品（例如 6,800 的帳篷）會讓找零算不出來、圖文對不上。
const ITEM_NAME = "露營桌 蛋捲桌";
const ITEM_PRICE = "1800";
// 本次執行的識別碼：讓 fixture 的冪等鍵在同一次執行內穩定、跨次執行不相撞。
const RUN_ID = new Date().toISOString().replace(/\D/g, "").slice(0, 14);

async function ensureConsignmentItem() {
  const token = await apiLogin();
  const match = (items) =>
    items.find(
      (i) =>
        i.ownership_type === "CONSIGNMENT" &&
        i.status === "IN_STOCK" &&
        i.name === ITEM_NAME &&
        String(i.listed_price) === ITEM_PRICE,
    );

  const existing = match((await apiJson(token, "GET", "/api/v1/serialized-items?limit=200")).json ?? []);
  if (existing) return existing.item_code;

  // 收購/寄售對象**必須有 national_id**（後端 422），所以用 has_national_id 篩，
  // 不能隨手取第一個賣方。（寄售人已併入賣方，2026-09-01 裁示）
  //
  // **要用搜尋，不能讀前 50 筆**：demo 資料有三千多筆聯絡人，03-contacts 建的那位
  // 排在最後（id 3000+），`?limit=50` 撈到的全是前面的純會員 → 永遠找不到而中止。
  // 原本的寫法在只有十幾筆測資時可行，被 12 個月的真實資料打壞。
  const found = (await apiJson(token, "GET", "/api/v1/contacts?q=手冊測試客&limit=50")).json ?? [];
  let consignor = found.find((c) => (c.roles ?? []).includes("SELLER") && c.has_national_id);
  if (!consignor) {
    // 退而求其次：翻頁找任何一位具身分證的寄售人（seed 的賣方也有身分證）
    for (let offset = 0; offset < 4000 && !consignor; offset += 200) {
      const page = (await apiJson(token, "GET", `/api/v1/contacts?limit=200&offset=${offset}`)).json ?? [];
      if (page.length === 0) break;
      consignor = page.find((c) => (c.roles ?? []).includes("SELLER") && c.has_national_id);
    }
  }
  if (!consignor) {
    throw new Error("找不到具身分證字號的寄售人（寄售收購必填），請先跑 03-contacts");
  }

  // 冪等鍵**不可固定**：本節跑完那件就被賣掉，下次重跑又找不到在庫品而走到這裡；
  // 若沿用同一把鍵，後端會重播上一張收購、不會產生新庫存，於是再查仍是空 → 必然失敗。
  // 以時間戳成鍵：同一次執行內重試仍冪等，跨次執行則各自建立自己的 fixture。
  const created = await apiJson(
    token,
    "POST",
    "/api/v1/acquisitions",
    {
      type: "CONSIGNMENT",
      contact_id: consignor.id,
      items: [{ name: ITEM_NAME, grade: "A", listed_price: ITEM_PRICE, commission_pct: 50 }],
    },
    { "Idempotency-Key": `manual-08c-consignment-${RUN_ID}` },
  );
  if (created.status >= 400) {
    throw new Error(`補建寄售品失敗（HTTP ${created.status}）：${JSON.stringify(created.json)}`);
  }
  const fresh = match((await apiJson(token, "GET", "/api/v1/serialized-items?limit=200")).json ?? []);
  if (!fresh) throw new Error(`補建寄售品後仍找不到在庫的「${ITEM_NAME}」`);
  return fresh.item_code;
}

const consignmentCode = await ensureConsignmentItem();
note(`本節使用的寄售品：${consignmentCode}（05 建的那件已被 08-pos 交易 C 售出）`);

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

await page.fill(".pos-scan-input", consignmentCode); // 寄售的露營桌
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
