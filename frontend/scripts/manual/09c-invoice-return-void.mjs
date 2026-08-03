// 手冊 09c：開立發票的交易在「退貨」與「作廢」時，發票狀態如何變化（實機觀察）。
// **需要 Amego 測試憑證且發票真的開立成功**（以 MANUAL_ALLOW_EINVOICE_ISSUE 明確 opt-in）：
// 情境 A 會斷言發票為 ISSUED 並走完收回紙本＋顧客簽名同意的關卡，關卡缺席即視為失敗，
// 不會靜默略過——那是稅務關卡，寧可讓重跑紅燈也不該放行。
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

import { allowEInvoiceIssue, apiJson, apiLogin, BASE, makeShot, note, SHOTS_ROOT, shotsDir, statePath, withSettings } from "./_lib.mjs";

const dir = shotsDir("09-invoice-return-void");
const shot = makeShot(dir);
const acq = JSON.parse(readFileSync(join(SHOTS_ROOT, "05-acquisition", "data.json"), "utf8"));

const before = (await apiJson(await apiLogin(), "GET", "/api/v1/settings")).json;
if (before === null) throw new Error("讀不到原始設定");
if (before.einvoice_enabled) {
  throw new Error("電子發票原本即為啟用狀態：疑似正式環境，中止。");
}
if (!allowEInvoiceIssue("開啟電子發票、建立兩筆交易並退貨/作廢（會觸發開立）")) {
  process.exit(0);
}

async function signOnKiosk() {
  await kiosk.waitForSelector("canvas.kiosk-sign-canvas", { timeout: 30000 });
  const canvas = kiosk.locator("canvas.kiosk-sign-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  await kiosk.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.6);
  await kiosk.mouse.down();
  for (const [fx, fy] of [[0.35, 0.35], [0.5, 0.7], [0.65, 0.3], [0.8, 0.6]]) {
    await kiosk.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 14 });
  }
  await kiosk.mouse.up();
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
  // 同月整筆退貨會作廢原發票：必須先收回紙本證明聯並請客人於顧客螢幕簽名同意，
  // 否則「確認退貨」保持停用（見 09d-invoice-disposition 的完整示範）。
  //
  // **這道關卡是稅務關卡，不可以「找不到就跳過」**：後端 invoice_policy 只在發票未開立時
  // 回 NONE（兩旗標皆 false）而不顯示關卡。若因為憑證失效、平台拒收或前端誤刪而讓關卡消失，
  // 靜默略過會讓整輪重跑照樣全綠、把高風險迴歸蓋掉。所以先斷言發票確實是 ISSUED，
  // 再要求關卡一定存在；本腳本是以 MANUAL_ALLOW_EINVOICE_ISSUE 明確 opt-in 真的開票的，
  // 沒開成功就是環境或系統有問題，應該失敗而不是放行。
  const stateBeforeReturn = await saleState(saleA);
  if (stateBeforeReturn.invoice_status !== "ISSUED") {
    throw new Error(
      `#${saleA} 的發票未進入 ISSUED（實際：${JSON.stringify(stateBeforeReturn)}）；` +
        "本節需要真的開立成功的發票才能驗證作廢流程，請確認 Amego 測試憑證與佇列送出狀態。",
    );
  }
  const returnDialog = page.locator('[role="dialog"][aria-label="退貨"]');
  const paperCheckbox = returnDialog.getByLabel("已向客人收回發票證明聯（紙本）");
  await paperCheckbox.waitFor({ timeout: 15000 });
  await paperCheckbox.check();
  await returnDialog.locator('button:has-text("請客人於顧客螢幕簽名同意")').click();
  await kiosk.waitForSelector(".kiosk-snapshot", { timeout: 30000 });
  await kiosk.waitForTimeout(800);
  await signOnKiosk();
  await kiosk.locator('button:has-text("確認並送出")').click();
  await returnDialog.locator("text=客人已簽名同意").waitFor({ timeout: 25000 });
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
