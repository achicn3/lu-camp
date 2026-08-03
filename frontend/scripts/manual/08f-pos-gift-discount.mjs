// 手冊 08f：POS 贈品與臨時折扣（docs/32）。
// 實測：加入兩樣商品 → 單品折扣 → 整單折扣 → 把一列改為贈品 → 現金結帳 →
// 到交易紀錄示範「退主商品但贈品未退」的提示。全程截圖供手冊使用。
import { existsSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import {
  apiJson,
  apiLogin,
  BASE,
  login,
  makeShot,
  note,
  SHOTS_ROOT,
  shotsDir,
  statePath,
} from "./_lib.mjs";

const dir = shotsDir("08-pos-gift-discount");
const shot = makeShot(dir);

const token = await apiLogin();

// 本節需要**兩個**有庫存的一般商品（單品折扣一個、改為贈品一個）。
// 06-inventory 只上架一個且庫存 0，11-purchasing 只補同一個且順序在後——乾淨資料庫必然不足。
// 因此不足時就自己補：走正規的「上架 → 建採購單 → 送出 → 收貨入庫」，資料與店員手動操作
// 產生的完全一樣（手冊仍是照真實資料截圖），並非塞假庫存。
const stamp = "manual08f";
const SUPPLIER_NAME = "手冊測試補貨商";

async function listUsable() {
  return ((await apiJson(token, "GET", "/api/v1/catalog-products?limit=100")).json ?? []).filter(
    (p) => p.quantity_on_hand >= 3 && Number(p.unit_price) > 0,
  );
}

// 每個 mutation 都檢查 HTTP 狀態：中途失敗要當場停下，否則會留下半套資料
// （建好供應商/採購單卻沒收貨），重跑時又疊一組，污染 11-purchasing 與報表。
async function must(label, promise) {
  const res = await promise;
  if (res.status >= 400) {
    throw new Error(`${label} 失敗（HTTP ${res.status}）：${JSON.stringify(res.json)}`);
  }
  return res.json;
}

async function ensureTwoStockedProducts() {
  if ((await listUsable()).length >= 2) return;
  const all = (await apiJson(token, "GET", "/api/v1/catalog-products?limit=100")).json ?? [];
  const pick = async (name, unitPrice, reorder) =>
    all.find((p) => p.name === name) ??
    (await must(
      `建立一般商品 ${name}`,
      apiJson(
        token,
        "POST",
        "/api/v1/catalog-products",
        { name, unit_price: unitPrice, reorder_point: reorder },
        { "Idempotency-Key": `${stamp}-${name}` },
      ),
    ));

  const gas = await pick("高山瓦斯罐 230g", "180", 10);
  const battery = await pick("營燈電池 3號 4入", "120", 6);

  // 供應商沿用既有的同名那家，不要每次重跑都長一家新的。
  const suppliers = (await apiJson(token, "GET", "/api/v1/suppliers?limit=100")).json ?? [];
  const supplier =
    suppliers.find((s) => s.name === SUPPLIER_NAME) ??
    (await must("建立供應商", apiJson(token, "POST", "/api/v1/suppliers", { name: SUPPLIER_NAME })));

  // 若上次在「建了採購單但還沒收貨」時中斷，先把那張撿回來收掉，不要另建一張。
  const pending = ((await apiJson(token, "GET", "/api/v1/purchase-orders?limit=100")).json ?? [])
    .filter((p) => p.supplier_id === supplier.id && p.status === "ORDERED");
  const po =
    pending[0] ??
    (await must(
      "建立採購單",
      apiJson(token, "POST", "/api/v1/purchase-orders", {
        supplier_id: supplier.id,
        submit: true,
        lines: [
          { catalog_product_id: gas.id, qty: 24, unit_cost: "120" },
          { catalog_product_id: battery.id, qty: 12, unit_cost: "80" },
        ],
      }),
    ));

  await must(
    "收貨入庫",
    apiJson(
      token,
      "POST",
      `/api/v1/purchase-orders/${po.id}/receive`,
      { lines: po.lines.map((l) => ({ line_id: l.id, qty: l.qty - (l.received_qty ?? 0) })) },
      { "Idempotency-Key": `${stamp}-recv-${po.id}` },
    ),
  );
  note(`一般商品庫存不足 → 已依採購/收貨流程補足（採購單 #${po.id}）`);
}

await ensureTwoStockedProducts();
const products = await listUsable();
if (products.length < 2) {
  throw new Error("補貨後仍湊不到兩個有庫存的一般商品，請檢查採購/收貨流程");
}
const [productA, productB] = products;
note(`使用商品：${productA.name} / ${productB.name}`);

// 沿用既有登入態；單獨執行本節（前面章節未跑）時自行登入。
const statefile = statePath("staff-state.json");
const hasState = existsSync(statefile);
const browser = await chromium.launch();
const ctx = await browser.newContext({
  ...(hasState ? { storageState: statefile } : {}),
  viewport: { width: 1440, height: 950 },
  deviceScaleFactor: 2,
  locale: "zh-TW",
  timezoneId: "Asia/Taipei",
});
const page = await ctx.newPage();
if (!hasState) await login(page);
page.on("pageerror", (e) => console.log(`⚠ POS JS 錯誤 ${e}`));

async function clearCart() {
  // POS 會從既有的購物車 session 還原**收款方式**。若前一支腳本（08e）停在
  // 「購物金＋其他付款」，只移除品項會留著該模式，本節結帳鈕就會被混合付款驗證卡住。
  // 注意：這種情況下購物車仍是 DRAFT，畫面上**沒有**「開始下一筆」可按（那顆只在
  // 交易完成後出現），所以必須直接把收款方式切回現金——實測點一下就會恢復。
  const cash = page.locator('.pos-tender-mode:has-text("現金")').first();
  if ((await cash.count()) > 0) {
    await cash.click();
    await page.waitForTimeout(1200);
  }
  const removeButtons = page.locator('.pos-cart button:has-text("移除")');
  while ((await removeButtons.count()) > 0) {
    await removeButtons.first().click();
    await page.waitForTimeout(700);
  }
  // 會員歸戶也會跟著還原；取消掉才不會把上一筆的購物金餘額帶進本節畫面。
  const cancelMember = page.locator('button:has-text("取消歸戶")');
  if ((await cancelMember.count()) > 0) {
    await cancelMember.click();
    await page.waitForTimeout(1200);
  }
  await page.waitForTimeout(1200);
}

async function addBySku(sku, expectedRows) {
  await page.waitForFunction(
    () => document.querySelector('input[name="code"]')?.disabled === false,
    undefined,
    { timeout: 20000 },
  );
  await page.fill(".pos-scan-input", sku);
  await page.keyboard.press("Enter");
  await page.waitForSelector(`.pos-cart tbody tr:nth-child(${expectedRows})`, {
    timeout: 20000,
  });
  await page.waitForTimeout(1500);
}

await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await clearCart();

// ── 購物車：兩樣商品 ──
await addBySku(productA.sku, 1);
await addBySku(productB.sku, 2);
await shot(page, "cart", { locator: ".pos-grid" });

// ── 單品折扣 ──
await page.locator(`button[aria-label="折扣 ${productA.name}"]`).click();
await page.waitForSelector('[role="dialog"][aria-label="新增折扣"]');
await page.fill('input[aria-label="折扣數值"]', "50");
await page.waitForTimeout(600);
await shot(page, "item-discount-dialog", { locator: ".pos-dialog" });
await page.click('button:has-text("套用折扣")');
await page.waitForTimeout(2500);
await shot(page, "item-discount-applied", { locator: ".pos-right" });

// ── 整單折扣 ──
await page.click('button:has-text("整單折扣")');
await page.waitForSelector('[role="dialog"][aria-label="新增折扣"]');
await page.locator('input[type="radio"]').nth(1).check();
await page.fill('input[aria-label="折扣數值"]', "10");
await page.waitForTimeout(600);
await shot(page, "order-discount-dialog", { locator: ".pos-dialog" });
await page.click('button:has-text("套用折扣")');
await page.waitForTimeout(2500);
await shot(page, "discount-list", { locator: ".pos-discount-panel" });

// ── 改為贈品 ──
await page.locator(`button[aria-label="改為贈品 ${productB.name}"]`).click();
await page.waitForSelector('[role="dialog"][aria-label="改為贈品"]');
await page.selectOption('[role="dialog"] select', { index: 1 });
await page.waitForTimeout(600);
await shot(page, "gift-dialog", { locator: ".pos-dialog" });
await page.click('button:has-text("確認贈送")');
await page.waitForSelector(".pos-gift-badge");
await page.waitForSelector(".pos-summary", { timeout: 20000 });
await page.waitForTimeout(2000);
await shot(page, "gift-cart", { locator: ".pos-grid" });
await shot(page, "summary", { locator: ".pos-right" });

// ── 現金結帳 ──
await page.locator('.pos-tender-mode:has-text("現金")').first().click();
await page.waitForTimeout(1000);
await page.click('button:has-text("結帳")');
await page.waitForSelector(".pos-complete", { timeout: 30000 });
await page.waitForTimeout(1500);
await shot(page, "completed", { locator: ".pos-complete" });

// ── 交易紀錄：退主商品但贈品未退 ──
await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await page.locator("table tbody tr").first().locator('button:has-text("退貨")').click();
const dialog = page.locator('[role="dialog"]');
await dialog.waitFor({ state: "visible" });
await page.waitForTimeout(1500);
await dialog.locator(".return-qty-input").first().fill("1");
await dialog.locator('input[type="text"]').first().fill("尺寸不合");
await dialog.locator(".return-gift-notice").waitFor({ state: "visible", timeout: 20000 });
await page.waitForTimeout(1200);
await shot(page, "return-gift-notice", { locator: ".pos-dialog, .card" });

await browser.close();
note(`08f 完成，截圖於 ${join(SHOTS_ROOT, "08-pos-gift-discount")}`);
