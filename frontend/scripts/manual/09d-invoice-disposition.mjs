// 手冊 09d：已開立發票的交易退貨時，系統怎麼處置發票（作廢／折讓）與店員要做的兩件事。
//
// **測試資料佈置的誠實說明**：本機沒有電子發票平台憑證，發票無法真的被平台核可。為了拍到
// 「已開立發票被退貨」的畫面，本腳本以 SQL 把**自己剛建立的那幾筆**測試交易的發票標記為
// 已開立（見 markIssued）。被驗證的是**判定規則與店員操作流程**（作廢或折讓、收回紙本、
// 簽名同意）；與平台之間的實際往返未在此驗證，手冊內文亦如此註明。
//
// 只能對本機拋棄式環境（專用資料庫 lucamp_manual）執行。
import { execFileSync } from "node:child_process";

import { chromium } from "playwright";

import {
  allowEInvoiceIssue,
  apiJson,
  apiLogin,
  BASE,
  makeShot,
  note,
  shotsDir,
  statePath,
  withSettings,
} from "./_lib.mjs";

const DB_CONTAINER = process.env.MANUAL_DB_CONTAINER ?? "lu-camp-db-1";
const DB_NAME = process.env.MANUAL_DB_NAME ?? "lucamp_manual";

const dir = shotsDir("09-invoice-disposition");
const shot = makeShot(dir);

const token0 = await apiLogin();
const before = (await apiJson(token0, "GET", "/api/v1/settings")).json;
if (before === null) throw new Error("讀不到原始設定");
if (before.einvoice_enabled) {
  throw new Error("電子發票原本即為啟用狀態：疑似正式環境，中止。");
}
if (!allowEInvoiceIssue("開啟電子發票、建立數筆交易並退貨（會觸發開立請求）")) {
  process.exit(0);
}

function sql(statement) {
  return execFileSync(
    "docker",
    ["exec", DB_CONTAINER, "psql", "-U", "lucamp", "-d", DB_NAME, "-tAc", statement],
    { encoding: "utf8" },
  ).trim();
}

/** 把該銷售的發票標記為已開立（測試資料佈置，見檔頭）。 */
function markIssued(saleId, { invoiceNo, date, printMark = true, carrier = null }) {
  sql(
    `UPDATE invoices SET status='ISSUED', invoice_no='${invoiceNo}', invoice_date='${date}',` +
      ` print_mark=${printMark}, carrier_type=${carrier ? `'${carrier}'` : "NULL"}` +
      ` WHERE sale_id=${saleId}`,
  );
  sql(`UPDATE sales SET invoice_status='ISSUED' WHERE id=${saleId}`);
}

const taipeiToday = () => new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);
const taipeiLastMonth = () => {
  const now = new Date(Date.now() + 8 * 3600_000);
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 15))
    .toISOString()
    .slice(0, 10);
};

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

async function openReturn(saleId) {
  await page.goto(`${BASE}/sales`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(`button[aria-label="退貨銷售 ${saleId}"]`, { timeout: 20000 });
  await page.locator(`button[aria-label="退貨銷售 ${saleId}"]`).click();
  await page.waitForSelector(".return-lines-table", { timeout: 15000 });
  return page.locator('[role="dialog"][aria-label="退貨"]');
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

await withSettings(["einvoice_enabled"], async () => {
  const token = await apiLogin();
  await apiJson(token, "PATCH", "/api/v1/settings", { einvoice_enabled: true });
  note("已啟用電子發票");

  const stamp = Date.now().toString().slice(-8);
  const product = (
    await apiJson(token, "POST", "/api/v1/catalog-products", {
      sku: `MAN-INV-${stamp}`,
      name: `手冊示範商品-${stamp}`,
      unit_price: "500",
    })
  ).json;
  const supplier = (
    await apiJson(token, "POST", "/api/v1/suppliers", { name: `手冊示範供應商-${stamp}` })
  ).json;
  const po = (
    await apiJson(token, "POST", "/api/v1/purchase-orders", {
      supplier_id: supplier.id,
      submit: true,
      lines: [{ catalog_product_id: product.id, qty: 30, unit_cost: "200" }],
    })
  ).json;
  await apiJson(
    token,
    "POST",
    `/api/v1/purchase-orders/${po.id}/receive`,
    { lines: [{ line_id: po.lines[0].id, qty: 30 }] },
    { "Idempotency-Key": `man-inv-recv-${stamp}` },
  );

  async function makeSale(qty, key, tenders = null) {
    const body = {
      lines: [{ line_type: "CATALOG", catalog_product_id: product.id, qty }],
      expected_einvoice_enabled: true,
    };
    if (tenders) body.tenders = tenders;
    return (
      await apiJson(token, "POST", "/api/v1/sales", body, {
        "Idempotency-Key": `man-inv-sale-${key}-${stamp}`,
      })
    ).json;
  }

  // ── 情境一：同月整筆退貨 → 作廢原發票 ───────────────────────────────────
  const saleA = await makeSale(1, "a");
  markIssued(saleA.id, { invoiceNo: `AB${stamp}`, date: taipeiToday() });
  let dialog = await openReturn(saleA.id);
  await dialog.locator('button:has-text("整筆退貨")').click();
  await dialog.locator('input[placeholder^="例：尺寸不合"]').fill("尺寸不合，整筆退");
  await page.waitForSelector("text=作廢原發票", { timeout: 20000 });
  await shot(page, "void-notice", { locator: ".pos-dialog" });
  note(`情境一 #${saleA.id}：顯示作廢提示，確認鈕停用＝${await dialog.locator('button:has-text("確認退貨")').isDisabled()}`);

  await dialog.getByLabel("已向客人收回發票證明聯（紙本）").check();
  await shot(page, "paper-checked-still-blocked", { locator: ".pos-dialog" });

  await dialog.locator('button:has-text("請客人於顧客螢幕簽名同意")').click();
  await kiosk.waitForSelector(".kiosk-snapshot", { timeout: 30000 });
  await kiosk.waitForTimeout(800);
  await shot(kiosk, "kiosk-consent", { full: true });
  await signOnKiosk();
  await shot(kiosk, "kiosk-signed", { full: true });
  await kiosk.locator('button:has-text("確認並送出")').click();

  await dialog.locator("text=客人已簽名同意").waitFor({ timeout: 25000 });
  await shot(page, "ready-to-submit", { locator: ".pos-dialog" });
  await dialog.locator('button:has-text("確認退貨")').click();
  await page.waitForSelector("text=退貨完成", { timeout: 25000 });
  await shot(page, "return-done", { content: true });
  note(`情境一完成：${sql(`SELECT status || '/' || coalesce(void_reason,'-') FROM invoices WHERE sale_id=${saleA.id}`)}`);

  // ── 情境二：部分退貨 → 開立折讓單（不要求收回紙本）─────────────────────
  const saleB = await makeSale(3, "b");
  markIssued(saleB.id, { invoiceNo: `AC${stamp}`, date: taipeiToday() });
  dialog = await openReturn(saleB.id);
  await dialog.locator("input.return-qty-input").first().fill("1");
  await dialog.locator('input[placeholder^="例：尺寸不合"]').fill("只退一件");
  await page.waitForSelector("text=開立折讓單", { timeout: 20000 });
  await shot(page, "allowance-partial", { locator: ".pos-dialog" });
  await dialog.locator('button:has-text("取消")').click();

  // ── 情境三：跨月整筆退貨 → 改開折讓單 ─────────────────────────────────
  const saleC = await makeSale(1, "c");
  markIssued(saleC.id, { invoiceNo: `AD${stamp}`, date: taipeiLastMonth() });
  dialog = await openReturn(saleC.id);
  await dialog.locator('button:has-text("整筆退貨")').click();
  await dialog.locator('input[placeholder^="例：尺寸不合"]').fill("上個月買的，整筆退");
  await page.waitForSelector("text=已跨月", { timeout: 20000 });
  await shot(page, "allowance-cross-month", { locator: ".pos-dialog" });
  await dialog.locator('button:has-text("取消")').click();

  // ── 情境四：載具發票整筆退貨 → 作廢但無紙本可收回 ─────────────────────
  const saleD = await makeSale(1, "d");
  markIssued(saleD.id, {
    invoiceNo: `AE${stamp}`,
    date: taipeiToday(),
    printMark: false,
    carrier: "3J0002",
  });
  dialog = await openReturn(saleD.id);
  await dialog.locator('button:has-text("整筆退貨")').click();
  await dialog.locator('input[placeholder^="例：尺寸不合"]').fill("載具發票整筆退");
  await page.waitForSelector("text=無紙本須收回", { timeout: 20000 });
  await shot(page, "void-carrier-no-paper", { locator: ".pos-dialog" });
  await dialog.locator('button:has-text("取消")').click();

  // ── 情境五：台灣Pay 整筆退貨 → 三個確認同時出現 ───────────────────────
  const saleE = await makeSale(1, "e", [{ tender_type: "TAIWAN_PAY", amount: "500" }]);
  markIssued(saleE.id, { invoiceNo: `AF${stamp}`, date: taipeiToday() });
  dialog = await openReturn(saleE.id);
  await dialog.locator('button:has-text("整筆退貨")').click();
  await dialog.locator('input[placeholder^="例：尺寸不合"]').fill("台灣Pay 整筆退");
  await page.waitForSelector("text=作廢原發票", { timeout: 20000 });
  await shot(page, "taiwanpay-three-confirmations", { locator: ".pos-dialog" });
  await dialog.locator('button:has-text("取消")').click();
});

await browser.close();
console.log("✅ 09d 完成");
