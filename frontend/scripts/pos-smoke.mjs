// POS 結帳瀏覽器煙霧測試：API 準備唯一測試資料 → 登入 → /pos →
// 已售/售罄阻擋、序號品重複掃碼、現金/購物金/混合付款、散裝批數量調整、列印明細。
// 另用既有 /sales API 驗證 catalog 數量型商品銷售，因目前 POS UI 尚無 catalog picker。
// 執行：node scripts/pos-smoke.mjs
// 需 backend:8000 + frontend:3000 已起、dev-manager 帳號可登入，且至少一筆可售 catalog SKU 已 seed。
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";
import { validNationalId } from "./_national-id.mjs";

const BASE = stripTrailingSlash(process.env.SMOKE_BASE ?? "http://localhost:3000");
const API_BASE = stripTrailingSlash(process.env.SMOKE_API_BASE ?? "http://localhost:8000");
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "codex-test", "pos-smoke");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

function stripTrailingSlash(value) {
  return value.replace(/\/+$/, "");
}

function idem(label) {
  return `pos-${label}-${randomUUID()}`.slice(0, 80);
}

function money(amount) {
  return Number(amount).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function parseNtd(value) {
  return Number.parseInt(String(value).replace(/,/g, ""), 10);
}

async function apiJson(
  path,
  { method = "GET", token = null, body = undefined, headers = {}, expected = [200] } = {},
) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(`${method} ${path} returned non-JSON (${response.status}): ${text}`);
    }
  }
  if (!expected.includes(response.status)) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? JSON.stringify(data.detail)
        : text;
    throw new Error(
      `${method} ${path} expected ${expected.join("/")} got ${response.status}: ${detail}`,
    );
  }
  return data;
}

async function loginApi() {
  const data = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: USERNAME, password: PASSWORD },
  });
  if (!data?.access_token) throw new Error("登入 API 未回傳 access_token");
  return data.access_token;
}

async function ensureOpenCashSession(token) {
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current !== null) return current;
  return await apiJson("/api/v1/cash-sessions/open", {
    method: "POST",
    token,
    body: { opening_float: "2000" },
    expected: [201],
  });
}

async function createContact(token, body) {
  return await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body,
    expected: [201],
  });
}

async function createSeller(token, runId) {
  return await createContact(token, {
    name: `POS 煙霧賣方 ${runId}`,
    national_id: validNationalId(),
    roles: ["SELLER"],
    member_points: 0,
    source_note: "POS smoke setup",
  });
}

async function createMemberWithCredit(token, runId, minBalance) {
  const member = await createContact(token, {
    name: `POS 煙霧會員 ${runId}`,
    phone: `09${runId.replace(/\D/g, "").slice(-8).padStart(8, "0")}`,
    roles: ["MEMBER"],
    member_points: 0,
    source_note: "POS smoke setup",
  });
  const balance = await apiJson(`/api/v1/contacts/${member.id}/store-credit`, { token });
  const current = parseNtd(balance.balance);
  if (current < minBalance) {
    await apiJson(`/api/v1/contacts/${member.id}/store-credit/adjustments`, {
      method: "POST",
      token,
      headers: { "Idempotency-Key": idem("credit") },
      body: {
        amount: String(minBalance - current),
        reason: "POS smoke store credit setup",
      },
      expected: [201],
    });
  }
  return member;
}

async function createAcquisition(token, body, label) {
  return await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": idem(label) },
    body,
    expected: [201],
  });
}

async function createSerializedFixture(token, sellerId, { name, price, cost = 100 }) {
  const result = await createAcquisition(
    token,
    {
      type: "BUYOUT",
      contact_id: sellerId,
      payout_method: "CASH",
      note: "POS smoke serialized fixture",
      items: [
        {
          name,
          grade: "A",
          listed_price: String(price),
          acquisition_cost: String(cost),
        },
      ],
    },
    "serialized",
  );
  const code = result.item_codes?.[0];
  if (!code) throw new Error("收購序號品未回傳 item_code");
  return { code, name, price };
}

async function createBulkFixture(
  token,
  sellerId,
  { name, unitPrice, totalQty, cost = unitPrice * totalQty },
) {
  const result = await createAcquisition(
    token,
    {
      type: "BULK_LOT",
      contact_id: sellerId,
      payout_method: "CASH",
      note: "POS smoke bulk fixture",
      lot: {
        name,
        acquisition_cost: String(cost),
        acquisition_basis: "BAG",
        total_qty: totalQty,
        unit_price: String(unitPrice),
        label: "POS smoke",
      },
    },
    "bulk",
  );
  if (!result.lot_code) throw new Error("收購散裝批未回傳 lot_code");
  const lot = await apiJson(
    `/api/v1/bulk-lots/by-code/${encodeURIComponent(result.lot_code)}`,
    { token },
  );
  return {
    code: result.lot_code,
    id: lot.id,
    name,
    unitPrice,
    totalQty,
  };
}

async function createSale(token, body, label) {
  return await apiJson("/api/v1/sales", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": idem(label) },
    body,
    expected: [200, 201],
  });
}

async function exerciseCatalogQuantitySale(token) {
  const products = await apiJson("/api/v1/catalog-products?limit=200", { token });
  const product = products.find((p) => p.quantity_on_hand > 0 && parseNtd(p.unit_price) > 0);
  if (!product) {
    throw new Error(
      "找不到可售數量型商品；請先 seed 至少一筆 /catalog-products quantity_on_hand > 0",
    );
  }
  const qty = product.quantity_on_hand >= 2 ? 2 : 1;
  const total = parseNtd(product.unit_price) * qty;
  const sale = await createSale(
    token,
    {
      lines: [{ line_type: "CATALOG", catalog_product_id: product.id, qty }],
      tenders: [{ tender_type: "CASH", amount: String(total) }],
    },
    "catalog",
  );
  const line = sale.lines?.find((l) => l.line_type === "CATALOG");
  ok(
    "數量型商品 API 結帳",
    sale.total === String(total) && line?.qty === qty,
    `${product.sku} × ${qty} = ${money(total)}`,
  );
}

async function prepareFixtures() {
  const token = await loginApi();
  await ensureOpenCashSession(token);
  const runId = `${Date.now()}-${randomUUID().slice(0, 6)}`;
  const seller = await createSeller(token, runId);
  const member = await createMemberWithCredit(token, runId, 5000);

  const cashItem = await createSerializedFixture(token, seller.id, {
    name: `POS 現金帳篷 ${runId}`,
    price: 1800,
    cost: 700,
  });
  const mixedItem = await createSerializedFixture(token, seller.id, {
    name: `POS 混合外套 ${runId}`,
    price: 500,
    cost: 200,
  });
  const soldItem = await createSerializedFixture(token, seller.id, {
    name: `POS 已售擋測 ${runId}`,
    price: 300,
    cost: 100,
  });
  await createSale(
    token,
    {
      lines: [{ line_type: "SERIALIZED", item_code: soldItem.code, qty: 1 }],
      tenders: [{ tender_type: "CASH", amount: String(soldItem.price) }],
    },
    "presold-serialized",
  );

  const storeCreditBulk = await createBulkFixture(token, seller.id, {
    name: `POS 散裝營釘 ${runId}`,
    unitPrice: 120,
    totalQty: 5,
  });
  const soldOutBulk = await createBulkFixture(token, seller.id, {
    name: `POS 售罄擋測 ${runId}`,
    unitPrice: 80,
    totalQty: 1,
  });
  await createSale(
    token,
    {
      lines: [{ line_type: "BULK_LOT", bulk_lot_id: soldOutBulk.id, qty: 1 }],
      tenders: [{ tender_type: "CASH", amount: String(soldOutBulk.unitPrice) }],
    },
    "presold-bulk",
  );

  await exerciseCatalogQuantitySale(token);
  ok("API 測試資料準備完成", true, `member=${member.name}`);
  return { member, cashItem, mixedItem, soldItem, storeCreditBulk, soldOutBulk };
}

async function loginBrowser(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);
}

async function scanCode(page, code) {
  await page.fill('input[name="code"]', code);
  await page.press('input[name="code"]', "Enter");
}

async function expectAlert(page, pattern) {
  const alert = page.locator('[role="alert"]').filter({ hasText: pattern });
  await alert.waitFor({ state: "visible" });
  return await alert.textContent();
}

async function expectTotal(page, amount, label) {
  await page.waitForFunction(
    (expected) => document.querySelector(".pos-total strong")?.textContent?.includes(expected),
    money(amount),
  );
  const total = await page.locator(".pos-total strong").textContent();
  ok(label, total?.includes(money(amount)) ?? false, total ?? "");
}

async function selectMember(page, member) {
  await page.locator(".pos-member-search input").fill(member.name);
  await page.click('button:has-text("查詢會員")');
  await page
    .locator(".pos-member-results button", { hasText: member.name })
    .first()
    .click();
  await page.waitForSelector(".pos-member-selected");
  await page.waitForSelector(".pos-member-selected .money");
  ok(
    "會員歸戶＋購物金餘額載入",
    await page.locator(".pos-member-selected", { hasText: member.name }).isVisible(),
  );
}

async function chooseTender(page, text) {
  await page.locator(".pos-tender-mode", { hasText: text }).click();
}

async function closePrintDialog(page) {
  const dialog = page.locator('[role="dialog"]');
  if (await dialog.isVisible()) {
    await dialog.locator("button").filter({ hasText: /完成/ }).last().click();
    await dialog.waitFor({ state: "hidden" });
  }
}

async function paymentMethodVisible(page, label) {
  return await page
    .locator(".pos-complete .stat-list dd")
    .filter({ hasText: new RegExp(`^${label}$`) })
    .isVisible();
}

async function startNextSale(page) {
  await closePrintDialog(page);
  await page.click('button:has-text("開始下一筆")');
  await page.waitForSelector("text=掃描或輸入商品條碼開始結帳");
}

let browser;
try {
  const fixtures = await prepareFixtures();

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

  // 1) 登入與 POS 空狀態
  await loginBrowser(page);
  await page.click('a:has-text("POS 結帳")');
  await page.waitForURL(`${BASE}/pos`);
  await page.waitForSelector("text=本期不開票");
  ok("POS 載入＋發票區隱藏（本期不開票）", true);
  ok("空車提示", await page.locator("text=掃描或輸入商品條碼開始結帳").isVisible());
  ok("空車時結帳鍵停用", await page.locator('button:has-text("結帳")').isDisabled());
  await page.screenshot({ path: `${SHOTS}/01-pos-empty.png` });

  // 2) 已售序號品 / 售罄散裝批阻擋
  await scanCode(page, fixtures.soldItem.code);
  const soldText = await expectAlert(page, /非在庫/);
  ok("已售序號品掃碼阻擋", soldText?.includes(fixtures.soldItem.code) ?? false, soldText ?? "");
  await scanCode(page, fixtures.soldOutBulk.code);
  const soldOutText = await expectAlert(page, /已售罄/);
  ok(
    "無庫存散裝批掃碼阻擋",
    soldOutText?.includes(fixtures.soldOutBulk.code) ?? false,
    soldOutText ?? "",
  );
  await page.screenshot({ path: `${SHOTS}/02-pos-stock-blocked.png` });

  // 3) 序號品加入、重複掃碼、購物金無會員阻擋
  await scanCode(page, fixtures.cashItem.code);
  await page.waitForSelector(`text=${fixtures.cashItem.name}`);
  ok("掃描序號品加入購物車", true);
  await expectTotal(page, fixtures.cashItem.price, "序號品應付總額正確");
  await scanCode(page, fixtures.cashItem.code);
  const duplicateText = await expectAlert(page, /序號品不可重複/);
  ok("重複掃碼阻擋", duplicateText?.includes("已在購物車") ?? false, duplicateText ?? "");
  await chooseTender(page, "購物金");
  const noMemberText = await expectAlert(page, /必須先指定買方會員/);
  ok(
    "購物金無會員 → 阻擋並停用結帳",
    (await page.locator('button:has-text("結帳")').isDisabled()) &&
      noMemberText?.includes("買方會員"),
    noMemberText ?? "",
  );
  await chooseTender(page, "現金");
  await page.screenshot({ path: `${SHOTS}/03-pos-duplicate.png` });

  // 4) 現金結帳 + 列印明細
  await page.click('button:has-text("結帳")');
  await page.waitForSelector("text=已完成");
  ok("現金結帳完成", await paymentMethodVisible(page, "現金"));
  ok("列印明細對話框跳出", await page.locator('[role="dialog"]:has-text("列印商品明細？")').isVisible());
  await page.screenshot({ path: `${SHOTS}/04-pos-cash-complete.png` });
  await page.click('[role="dialog"] button:has-text("列印明細")');
  await page.waitForSelector("text=已送出列印");
  ok("列印明細送出（稽核）", true);
  await startNextSale(page);

  // 5) 散裝批掃碼 + 數量調整 + 會員歸戶 + 純購物金付款
  await scanCode(page, fixtures.storeCreditBulk.code);
  await page.waitForSelector(`text=${fixtures.storeCreditBulk.name}`);
  await page.getByLabel(`${fixtures.storeCreditBulk.name} 數量`).fill("3");
  await expectTotal(page, fixtures.storeCreditBulk.unitPrice * 3, "散裝批數量調整後總額正確");
  await selectMember(page, fixtures.member);
  await chooseTender(page, "購物金");
  ok("購物金扣抵提示", await page.locator("text=購物金扣抵").isVisible());
  await page.screenshot({ path: `${SHOTS}/05-pos-bulk-store-credit.png` });
  await page.click('button:has-text("結帳")');
  await page.waitForSelector("text=已完成");
  ok("購物金結帳完成", await paymentMethodVisible(page, "購物金"));
  await page.screenshot({ path: `${SHOTS}/06-pos-store-credit-complete.png` });
  await startNextSale(page);

  // 6) 混合付款：現金部分 + 購物金部分
  await scanCode(page, fixtures.mixedItem.code);
  await page.waitForSelector(`text=${fixtures.mixedItem.name}`);
  await selectMember(page, fixtures.member);
  await chooseTender(page, "混合");
  await page.locator('label:has-text("現金部分") input').fill("300");
  await page.locator('label:has-text("實收現金") input').fill("500");
  ok("混合付款扣抵提示", await page.locator("text=購物金扣抵").isVisible());
  ok("混合付款找零提示", await page.locator(".pos-change", { hasText: "找零" }).isVisible());
  await page.click('button:has-text("結帳")');
  await page.waitForSelector("text=已完成");
  ok("混合付款結帳完成", await paymentMethodVisible(page, "混合"));
  await page.screenshot({ path: `${SHOTS}/07-pos-mixed-complete.png` });
  await closePrintDialog(page);
} catch (error) {
  ok("流程中斷", false, String(error));
  if (browser) {
    const pages = browser.contexts().flatMap((context) => context.pages());
    const page = pages[0];
    if (page) await page.screenshot({ path: `${SHOTS}/99-failure.png` });
  }
} finally {
  if (browser) await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n結果：${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length ? 1 : 0);
