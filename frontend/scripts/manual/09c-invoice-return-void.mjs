// 手冊 09c：開立發票的交易在「退貨」與「作廢」時，發票狀態如何變化（實機觀察）。
// 本機無 Amego 憑證，發票停在「發票開立中」；仍可如實驗證退貨/作廢時系統對發票的處置與畫面顯示。
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { BASE, SHOTS_ROOT, apiJson, apiLogin, makeShot, note, shotsDir, withSettings } from "./_lib.mjs";

const dir = shotsDir("09-invoice-return-void");
const shot = makeShot(dir);
const acq = JSON.parse(readFileSync(join(SHOTS_ROOT, "05-acquisition", "data.json"), "utf8"));

const before = (await apiJson(await apiLogin(), "GET", "/api/v1/settings")).json;
if (before === null) throw new Error("讀不到原始設定");
if (before.einvoice_enabled) {
  throw new Error("電子發票原本即為啟用狀態：疑似正式環境，中止。");
}

async function saleState(id) {
  const r = await apiJson(await apiLogin(), "GET", `/api/v1/sales/${id}`);
  return { status: r.json?.status, invoice_status: r.json?.invoice_status };
}
async function invoiceQueue() {
  const r = await apiJson(await apiLogin(), "GET", "/api/v1/einvoice/queue");
  return (r.json?.items ?? []).map((i) => `${i.action}/${i.message_type} ${i.status}`);
}

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
await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "domcontentloaded" });
await kiosk.waitForSelector(".kiosk-standby, .kiosk-cart-shell, .kiosk-task", { timeout: 30000 });

await withSettings(["einvoice_enabled"], async () => {
  // 開啟電子發票
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  await page.locator('input[name="einvoice_enabled"]').check();
  await page.click('button:has-text("儲存一般設定")');
  await page.waitForTimeout(2500);
  note("已啟用電子發票");

  async function makeSale(label) {
    await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);
    if ((await page.locator('button:has-text("開始下一筆")').count()) > 0) {
      await page.click('button:has-text("開始下一筆")');
      await page.waitForTimeout(2000);
    }
    const removeButtons = page.locator('.pos-cart button:has-text("移除")');
    while ((await removeButtons.count()) > 0) {
      await removeButtons.first().click();
      await page.waitForTimeout(800);
    }
    await page.fill(".pos-scan-input", acq.lotCode);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(2500);
    await page.click(".pos-checkout");
    await page.waitForSelector(".pos-complete", { timeout: 30000 });
    await page.waitForTimeout(5000);
    const text = await page.textContent(".pos-complete");
    const id = Number(/#(\d+)/.exec(text)?.[1]);
    note(`${label} 建立銷售 #${id}；完成畫面發票提示＝${(await page.textContent(".pos-invoice-note").catch(() => "—"))?.slice(0, 90)}`);
    await page.locator('.pos-dialog button:has-text("不用，完成")').click().catch(() => {});
    return id;
  }

  // ── 情境 A：開票交易 → 全額退貨 ──
  const saleA = await makeSale("情境A");
  note(`退貨前 #${saleA}：${JSON.stringify(await saleState(saleA))}；佇列＝${(await invoiceQueue()).join(", ")}`);
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  await shot(page, "sales-list-with-invoice", { content: true });
  await page.locator(`button[aria-label="退貨銷售 ${saleA}"]`).click();
  await page.waitForSelector(".return-lines-table", { timeout: 15000 });
  await page.waitForTimeout(1000);
  await page.click('button:has-text("整筆退貨")');
  await page.fill('.pos-dialog input[placeholder^="例：尺寸不合"]', "操作手冊示範：開票後退貨");
  await page.waitForTimeout(500);
  await shot(page, "return-dialog-invoiced", { locator: ".pos-dialog" });
  await page.click(".pos-dialog button.btn-danger");
  await page.waitForTimeout(5000);
  const afterReturn = await saleState(saleA);
  note(`退貨後 #${saleA}：${JSON.stringify(afterReturn)}；佇列＝${(await invoiceQueue()).join(", ")}`);
  await shot(page, "after-return", { content: true });

  // ── 情境 B：開票交易 → 作廢 ──
  const saleB = await makeSale("情境B");
  note(`作廢前 #${saleB}：${JSON.stringify(await saleState(saleB))}`);
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  await page.locator(`button[aria-label="作廢銷售 ${saleB}"]`).click();
  await page.waitForSelector('[aria-label="作廢銷售確認"]', { timeout: 15000 });
  await page.waitForTimeout(800);
  await shot(page, "void-dialog-invoiced", { locator: ".pos-dialog" });
  await page.locator(".pos-dialog button.btn-danger").click();
  await page.waitForTimeout(5000);
  const afterVoid = await saleState(saleB);
  note(`作廢後 #${saleB}：${JSON.stringify(afterVoid)}；佇列＝${(await invoiceQueue()).join(", ")}`);
  await shot(page, "after-void", { content: true });
});

await browser.close();
console.log("✅ 09c 完成");
