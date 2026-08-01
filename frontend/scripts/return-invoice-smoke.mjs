// 退貨的發票處置煙霧（ADR-014）：真 backend＋真 Postgres＋真瀏覽器，逐一走過六種情境並截圖。
//
// 本機沒有 Amego 憑證，發票無法真的送平台開立；為了驗證「已開立發票被退貨」的行為，
// 本腳本以 SQL 把測試交易的發票標記為已開立（僅動本次自己建的那幾張，見 markInvoice）。
// 這是**測試資料的佈置**，不是繞過流程：作廢／折讓的判定、簽名同意、紙本收回全部走真 API 與真 UI。
//
// 只能對本機拋棄式環境（lucamp_manual）執行。
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "return-invoice");
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";
const DB_CONTAINER = process.env.SMOKE_DB_CONTAINER ?? "lu-camp-db-1";
const DB_NAME = process.env.SMOKE_DB_NAME ?? "lucamp_manual";

for (const [label, url] of [["SMOKE_BASE", BASE], ["SMOKE_API_BASE", API]]) {
  const host = new URL(url).hostname;
  if (!["localhost", "127.0.0.1", "[::1]", "::1"].includes(host)) {
    throw new Error(`拒絕執行：${label}=${url} 非本機。此腳本會建單、退貨、改發票狀態。`);
  }
}

let passed = 0;
let failed = 0;
function ok(name, cond, detail = "") {
  if (cond) {
    passed += 1;
    console.log(`✅ ${name}${detail ? `：${detail}` : ""}`);
  } else {
    failed += 1;
    console.log(`❌ ${name}${detail ? `：${detail}` : ""}`);
  }
}

function sql(statement) {
  return execFileSync(
    "docker",
    ["exec", DB_CONTAINER, "psql", "-U", "lucamp", "-d", DB_NAME, "-tAc", statement],
    { encoding: "utf8" },
  ).trim();
}

async function apiJson(path, { method = "GET", token, body, idem } = {}) {
  const headers = { "content-type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (idem) headers["Idempotency-Key"] = idem;
  const r = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status}: ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

/** 把該銷售的發票標記為已開立（僅測試資料佈置，見檔頭說明）。 */
function markInvoice(saleId, { invoiceNo, date, printMark = true, carrier = null }) {
  sql(
    `UPDATE invoices SET status='ISSUED', invoice_no='${invoiceNo}', invoice_date='${date}',` +
      ` print_mark=${printMark}, carrier_type=${carrier ? `'${carrier}'` : "NULL"}` +
      ` WHERE sale_id=${saleId}`,
  );
  sql(`UPDATE sales SET invoice_status='ISSUED' WHERE id=${saleId}`);
}

function taipeiToday() {
  return new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);
}
function taipeiLastMonth() {
  const now = new Date(Date.now() + 8 * 3600_000);
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 15));
  return d.toISOString().slice(0, 10);
}

const browser = await chromium.launch();
const staffCtx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const kioskCtx = await browser.newContext({
  viewport: { width: 834, height: 1112 },
  hasTouch: true,
});
const page = await staffCtx.newPage();
const kiosk = await kioskCtx.newPage();
mkdirSync(SHOTS, { recursive: true });

let shotNo = 0;
async function shot(target, name) {
  shotNo += 1;
  const file = join(SHOTS, `${String(shotNo).padStart(2, "0")}-${name}.png`);
  await target.screenshot({ path: file, fullPage: true });
  console.log(`   📸 ${file}`);
}

async function openReturnDialog(saleId) {
  await page.goto(`${BASE}/sales`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(`button[aria-label="退貨銷售 ${saleId}"]`, { timeout: 15000 });
  await page.locator(`button[aria-label="退貨銷售 ${saleId}"]`).click();
  await page.waitForSelector('[role="dialog"][aria-label="退貨"]', { timeout: 8000 });
  return page.locator('[role="dialog"][aria-label="退貨"]');
}

/** 於顧客螢幕簽名並送出（真的在 canvas 上畫，後端要求可見墨跡）。 */
async function signOnKiosk() {
  await kiosk.waitForSelector("canvas.kiosk-sign-canvas", { timeout: 20000 });
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

try {
  const token = (
    await apiJson("/api/v1/auth/login", {
      method: "POST",
      body: { username: "dev-manager", password: PASS },
    })
  ).access_token;

  const settingsBefore = await apiJson("/api/v1/settings", { token });
  const stamp = Date.now().toString().slice(-8);

  // 開帳（已開帳則容忍）
  await apiJson("/api/v1/cash-sessions/open", {
    method: "POST",
    token,
    body: { opening_float: "5000" },
  }).catch(() => {});

  // 電子發票：開啟以產生發票列（收尾還原）
  await apiJson("/api/v1/settings", {
    method: "PATCH",
    token,
    body: { einvoice_enabled: true },
  });

  // 測試商品＋庫存
  const product = await apiJson("/api/v1/catalog-products", {
    method: "POST",
    token,
    body: { sku: `RINV-${stamp}`, name: `退貨發票測試品-${stamp}`, unit_price: "500" },
  });
  const supplier = await apiJson("/api/v1/suppliers", {
    method: "POST",
    token,
    body: { name: `退貨發票供應商-${stamp}` },
  });
  const po = await apiJson("/api/v1/purchase-orders", {
    method: "POST",
    token,
    body: {
      supplier_id: supplier.id,
      submit: true,
      lines: [{ catalog_product_id: product.id, qty: 40, unit_cost: "200" }],
    },
  });
  await apiJson(`/api/v1/purchase-orders/${po.id}/receive`, {
    method: "POST",
    token,
    idem: `rinv-recv-${stamp}`,
    body: { lines: [{ line_id: po.lines[0].id, qty: 40 }] },
  });

  async function makeSale(qty, key, { einvoice = true } = {}) {
    return apiJson("/api/v1/sales", {
      method: "POST",
      token,
      idem: `rinv-sale-${key}-${stamp}`,
      body: {
        lines: [{ line_type: "CATALOG", catalog_product_id: product.id, qty }],
        // POS 結帳當下觀察到的發票開關（後端會比對，防設定漂移）。
        expected_einvoice_enabled: einvoice,
      },
    });
  }

  // UI 登入
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('input[name="username"]', { timeout: 15000 });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', PASS);
  await page.click('button:has-text("登入")');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 20000 });

  // 顧客螢幕（kiosk）：登入待命
  await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "domcontentloaded" });
  await kiosk.waitForTimeout(1200);
  if (await kiosk.locator('input[name="username"]').count()) {
    await kiosk.fill('input[name="username"]', "dev-kiosk");
    await kiosk.fill('input[name="password"]', PASS);
    await kiosk.click('button:has-text("啟用裝置"), button:has-text("登入")');
    await kiosk.waitForTimeout(2500);
  }
  // 新的瀏覽器 context＝新裝置，需先與 POS 櫃檯配對（與門市首次安裝相同流程）。
  if (await kiosk.locator(".kiosk-pairing-code").count()) {
    const code = (await kiosk.textContent(".kiosk-pairing-code")).trim();
    await page.goto(`${BASE}/pos`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(".pos-kiosk-status", { timeout: 20000 });
    await page.fill(".pos-kiosk-status input", code);
    await page.click('.pos-kiosk-status button:has-text("配對")');
    await page.waitForSelector(".pos-kiosk-status.is-online", { timeout: 20000 });
  }
  await kiosk.waitForSelector(".kiosk-standby", { timeout: 25000 });
  ok("顧客螢幕已配對且待命", true);

  // ── 情境 1：同月整筆退貨 → 作廢原發票（紙本需收回、需簽名同意）────────────────
  const saleA = await makeSale(1, "a");
  markInvoice(saleA.id, { invoiceNo: `AB${stamp}`, date: taipeiToday() });
  let dialog = await openReturnDialog(saleA.id);
  await dialog.locator('button:has-text("整筆退貨")').click();
  await dialog.locator('input[placeholder*="尺寸不合"]').fill("尺寸不合，整筆退");
  await page.waitForSelector("text=作廢原發票", { timeout: 15000 });
  await shot(page, "s1-full-return-void-notice");
  const confirmA = dialog.locator('button:has-text("確認退貨")');
  ok("情境1：同月整筆退 → 顯示「作廢原發票」", await dialog.locator("b", { hasText: "作廢原發票" }).count() > 0);
  ok("情境1：未收回紙本＋未簽名 → 確認退貨鈕停用", await confirmA.isDisabled());
  ok(
    "情境1：明白告知缺什麼（收回紙本／簽名同意）",
    (await dialog.getByText("請先向客人收回發票證明聯（紙本）並勾選確認").count()) > 0 &&
      (await dialog.getByText("請先請客人於顧客螢幕簽名同意").count()) > 0,
  );

  await dialog.getByLabel("已向客人收回發票證明聯（紙本）").check();
  ok("情境1：只勾收回紙本仍不可送出（同意是另一道）", await confirmA.isDisabled());
  await shot(page, "s1-paper-recalled-still-blocked");

  await dialog.locator('button:has-text("請客人於顧客螢幕簽名同意")').click();
  await kiosk.waitForSelector(".kiosk-snapshot", { timeout: 25000 });
  await kiosk.waitForTimeout(800);
  await shot(kiosk, "s1-kiosk-consent-content");
  ok(
    "情境1：顧客螢幕顯示處置方式與退款金額",
    (await kiosk.getByText("作廢原發票").count()) > 0 &&
      (await kiosk.getByText("退款金額").count()) > 0,
  );
  await signOnKiosk();
  await shot(kiosk, "s1-kiosk-signed");
  await kiosk.locator('button:has-text("確認並送出")').click();

  await dialog.locator("text=客人已簽名同意").waitFor({ timeout: 20000 });
  await shot(page, "s1-consent-done-ready-to-submit");
  ok("情境1：簽名完成後可送出", await confirmA.isEnabled());
  await confirmA.click();
  await page.waitForSelector("text=退貨完成", { timeout: 20000 });
  await shot(page, "s1-return-done");

  const afterA = await apiJson(`/api/v1/sales/${saleA.id}`, { token });
  const invA = sql(`SELECT status || '/' || coalesce(void_reason,'-') FROM invoices WHERE sale_id=${saleA.id}`);
  ok("情境1：銷售轉為已退貨（非已作廢）", afterA.status === "RETURNED", afterA.status);
  ok("情境1：發票走作廢且原因為 FULL_RETURN", invA.startsWith("VOID") && invA.endsWith("FULL_RETURN"), invA);

  // ── 情境 2：部分退貨 → 折讓（不得要求收回紙本，仍須簽名）────────────────────
  const saleB = await makeSale(3, "b");
  markInvoice(saleB.id, { invoiceNo: `AC${stamp}`, date: taipeiToday() });
  dialog = await openReturnDialog(saleB.id);
  await dialog.locator("input.return-qty-input").first().fill("1");
  await dialog.locator('input[placeholder*="尺寸不合"]').fill("退一件");
  await page.waitForSelector("text=開立折讓單", { timeout: 15000 });
  await shot(page, "s2-partial-return-allowance");
  ok("情境2：部分退 → 顯示「開立折讓單」", (await dialog.locator("b", { hasText: "開立折讓單" }).count()) > 0);
  ok(
    "情境2：部分退不得要求收回紙本（原發票對未退商品仍有效）",
    (await dialog.getByLabel("已向客人收回發票證明聯（紙本）").count()) === 0,
  );
  ok("情境2：仍須簽名同意", (await dialog.getByText("請先請客人於顧客螢幕簽名同意").count()) > 0);
  await dialog.locator('button:has-text("取消")').click();

  // ── 情境 3：跨月整筆退貨 → 折讓 ─────────────────────────────────────────
  const saleC = await makeSale(1, "c");
  markInvoice(saleC.id, { invoiceNo: `AD${stamp}`, date: taipeiLastMonth() });
  dialog = await openReturnDialog(saleC.id);
  await dialog.locator('button:has-text("整筆退貨")').click();
  await dialog.locator('input[placeholder*="尺寸不合"]').fill("跨月整筆退");
  await page.waitForSelector("text=已跨月", { timeout: 15000 });
  await shot(page, "s3-cross-month-allowance");
  ok("情境3：跨月整筆退 → 改開折讓", (await dialog.locator("b", { hasText: "開立折讓單" }).count()) > 0);
  await dialog.locator('button:has-text("取消")').click();

  // ── 情境 4：載具發票整筆退貨 → 作廢但無紙本可收回 ────────────────────────
  const saleD = await makeSale(1, "d");
  markInvoice(saleD.id, {
    invoiceNo: `AE${stamp}`,
    date: taipeiToday(),
    printMark: false,
    carrier: "3J0002",
  });
  dialog = await openReturnDialog(saleD.id);
  await dialog.locator('button:has-text("整筆退貨")').click();
  await dialog.locator('input[placeholder*="尺寸不合"]').fill("載具整筆退");
  await page.waitForSelector("text=無紙本須收回", { timeout: 15000 });
  await shot(page, "s4-carrier-void-no-paper");
  ok("情境4：載具發票 → 作廢但不要求收回紙本",
    (await dialog.locator("b", { hasText: "作廢原發票" }).count()) > 0 &&
      (await dialog.getByLabel("已向客人收回發票證明聯（紙本）").count()) === 0);
  await dialog.locator('button:has-text("取消")').click();

  // ── 情境 5：已開過折讓的發票，之後退完剩餘 → 仍折讓，不得作廢原發票 ──────────
  const saleE = await makeSale(2, "e");
  markInvoice(saleE.id, { invoiceNo: `AF${stamp}`, date: taipeiToday() });
  const linesE = (await apiJson(`/api/v1/sales/${saleE.id}`, { token })).lines;
  const consentE1 = await apiJson("/api/v1/signing/tasks", {
    method: "POST",
    token,
    body: {
      kind: "RETURN_INVOICE_CONSENT",
      contact_id: null,
      content: { lines: [{ sale_line_id: linesE[0].id, qty: 1 }] },
      ref_type: "sale",
      ref_id: saleE.id,
    },
  });
  await kiosk.waitForSelector("canvas.kiosk-sign-canvas", { timeout: 25000 });
  await signOnKiosk();
  await kiosk.locator('button:has-text("確認並送出")').click();
  await kiosk.waitForTimeout(1500);
  await apiJson("/api/v1/returns", {
    method: "POST",
    token,
    idem: `rinv-e1-${stamp}`,
    body: {
      sale_id: saleE.id,
      reason: "先退一件",
      lines: [{ sale_line_id: linesE[0].id, qty: 1 }],
      consent_signature_task_id: consentE1.id,
    },
  });
  dialog = await openReturnDialog(saleE.id);
  await dialog.locator('button:has-text("整筆退貨")').click();
  await dialog.locator('input[placeholder*="尺寸不合"]').fill("退完剩餘");
  await page.waitForSelector("text=已開過折讓", { timeout: 15000 });
  await shot(page, "s5-prior-allowance-forces-allowance");
  ok("情境5：已開過折讓 → 後續一律折讓，不得作廢原發票",
    (await dialog.locator("b", { hasText: "開立折讓單" }).count()) > 0);
  await dialog.locator('button:has-text("取消")').click();

  // ── 情境 7：台灣Pay 整筆退 → 三重確認同時出現（手動退款＋收回紙本＋簽名同意）──────
  const saleG = await apiJson("/api/v1/sales", {
    method: "POST",
    token,
    idem: `rinv-sale-g-${stamp}`,
    body: {
      lines: [{ line_type: "CATALOG", catalog_product_id: product.id, qty: 1 }],
      tenders: [{ tender_type: "TAIWAN_PAY", amount: "500" }],
      expected_einvoice_enabled: true,
    },
  });
  markInvoice(saleG.id, { invoiceNo: `AG${stamp}`, date: taipeiToday() });
  dialog = await openReturnDialog(saleG.id);
  await dialog.locator('button:has-text("整筆退貨")').click();
  await dialog.locator('input[placeholder*="尺寸不合"]').fill("台灣Pay 整筆退");
  await page.waitForSelector("text=作廢原發票", { timeout: 15000 });
  await shot(page, "s7-taiwanpay-triple-confirmation");
  const twConfirm = dialog.locator('button:has-text("確認退貨")');
  ok(
    "情境7：台灣Pay＋作廢 → 手動退款確認與收回紙本兩個勾選同時出現",
    (await dialog.getByLabel(/已於台灣Pay完成退款/).count()) > 0 &&
      (await dialog.getByLabel("已向客人收回發票證明聯（紙本）").count()) > 0,
  );
  await dialog.getByLabel(/已於台灣Pay完成退款/).check();
  await dialog.getByLabel("已向客人收回發票證明聯（紙本）").check();
  ok("情境7：兩個勾選都完成、但未簽名仍不可送出", await twConfirm.isDisabled());
  await shot(page, "s7-taiwanpay-still-needs-consent");
  await dialog.locator('button:has-text("取消")').click();

  // ── 情境 6：沒有發票的交易 → 完全不出現發票處置區塊 ───────────────────────
  await apiJson("/api/v1/settings", { method: "PATCH", token, body: { einvoice_enabled: false } });
  const saleF = await makeSale(1, "f", { einvoice: false });
  dialog = await openReturnDialog(saleF.id);
  await dialog.locator('button:has-text("整筆退貨")').click();
  await dialog.locator('input[placeholder*="尺寸不合"]').fill("無發票整筆退");
  await page.waitForTimeout(1500);
  await shot(page, "s6-no-invoice-no-extra-steps");
  ok("情境6：無發票 → 不出現發票處置提示",
    (await dialog.locator('[aria-label="發票處置"]').count()) === 0);
  ok("情境6：無發票 → 可直接送出", await dialog.locator('button:has-text("確認退貨")').isEnabled());
  await dialog.locator('button:has-text("確認退貨")').click();
  await page.waitForSelector("text=退貨完成", { timeout: 20000 });
  await shot(page, "s6-return-done");

  // 還原全店設定（讀回驗證）
  await apiJson("/api/v1/settings", {
    method: "PATCH",
    token,
    body: { einvoice_enabled: settingsBefore.einvoice_enabled },
  });
  const settingsAfter = await apiJson("/api/v1/settings", { token });
  ok("收尾：電子發票開關已還原",
    settingsAfter.einvoice_enabled === settingsBefore.einvoice_enabled,
    String(settingsAfter.einvoice_enabled));
} catch (e) {
  failed += 1;
  console.log(`❌ 煙霧例外：${String(e).slice(0, 600)}`);
  await shot(page, "error-staff").catch(() => {});
  await shot(kiosk, "error-kiosk").catch(() => {});
} finally {
  await browser.close();
}

console.log(`\n通過 ${passed}／失敗 ${failed}　截圖：${SHOTS}`);
process.exit(failed > 0 ? 1 : 0);
