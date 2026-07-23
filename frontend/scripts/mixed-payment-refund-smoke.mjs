// 購物金＋台灣Pay 結帳／分次退貨瀏覽器 E2E：
// 1) 真 POS UI 建立 $400 混合付款（購物金 $300＋台灣Pay $100）。
// 2) 真列印請求帶交易編號與付款拆分。
// 3) /sales 第一次退 $200 全回購物金；第二次退 $200 回購物金 $100＋台灣Pay $100。
// 4) 同時保留桌機與手機版關鍵畫面，並由 API 核對最終帳本／退貨結果。
//
// 執行：node scripts/mixed-payment-refund-smoke.mjs
// 需 backend:8000、frontend:3000、hardware-agent:8001 與 dev-manager 測試帳號。
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS =
  process.env.SMOKE_SHOTS ?? resolve(homedir(), "tmp", "lu-camp-shots", "mixed-payment-refund");

mkdirSync(SHOTS, { recursive: true });

const checks = [];
function ok(name, pass, detail = "") {
  checks.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

function idempotencyKey(label) {
  return `mixed-refund-${label}-${randomUUID()}`.slice(0, 80);
}

async function apiJson(
  path,
  { method = "GET", token = null, body = undefined, headers = {}, expected = null } = {},
) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  const accepted = expected ?? [200, 201];
  if (!accepted.includes(response.status)) {
    throw new Error(`${method} ${path} → ${response.status}: ${text.slice(0, 500)}`);
  }
  return data;
}

function tenderMap(tenders) {
  return Object.fromEntries(tenders.map((tender) => [tender.tender_type, Number(tender.amount)]));
}

async function prepareFixtures(token) {
  const originalSettings = await apiJson("/api/v1/settings", { token });
  await apiJson("/api/v1/settings", {
    method: "PATCH",
    token,
    body: {
      einvoice_enabled: false,
      require_store_credit_signing: false,
      store_credit_min_spend: "0",
      linepay_enabled: true,
      linepay_fee_pct: "0.0150",
    },
  });

  const currentSession = await apiJson("/api/v1/cash-sessions/current", { token });
  if (currentSession === null) {
    await apiJson("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
    });
  }

  const stamp = `${Date.now()}-${randomUUID().slice(0, 6)}`;
  const phoneDigits = Date.now().toString().slice(-8);
  const member = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body: {
      name: `混合退款會員 ${stamp}`,
      phone: `09${phoneDigits}`,
      roles: ["MEMBER"],
      source_note: "mixed payment browser E2E",
    },
  });
  await apiJson(`/api/v1/contacts/${member.id}/store-credit/adjustments`, {
    method: "POST",
    token,
    headers: { "Idempotency-Key": idempotencyKey("credit") },
    body: { amount: "500", reason: "混合付款瀏覽器 E2E 備測" },
  });

  const sku = `MIX-REF-${Date.now()}`;
  const product = await apiJson("/api/v1/catalog-products", {
    method: "POST",
    token,
    body: { sku, name: `混合退款測試商品 ${stamp}`, unit_price: "200" },
  });
  const supplier = await apiJson("/api/v1/suppliers", {
    method: "POST",
    token,
    body: { name: `混合退款測試供應商 ${stamp}` },
  });
  const purchaseOrder = await apiJson("/api/v1/purchase-orders", {
    method: "POST",
    token,
    body: {
      supplier_id: supplier.id,
      submit: true,
      lines: [{ catalog_product_id: product.id, qty: 2, unit_cost: "100" }],
    },
  });
  await apiJson(`/api/v1/purchase-orders/${purchaseOrder.id}/receive`, {
    method: "POST",
    token,
    headers: { "Idempotency-Key": idempotencyKey("receive") },
    body: { lines: [{ line_id: purchaseOrder.lines[0].id, qty: 2 }] },
  });
  return { originalSettings, member, product };
}

async function loginBrowser(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 15_000 });
}

async function openReturnDialog(page, saleId) {
  await page.locator(`button[aria-label="退貨銷售 ${saleId}"]`).click();
  const dialog = page.locator('[role="dialog"][aria-label="退貨"]');
  await dialog.waitFor({ state: "visible", timeout: 8_000 });
  await dialog.locator("text=載入明細中…").waitFor({ state: "hidden", timeout: 8_000 });
  return dialog;
}

let browser;
let token;
let originalSettings;
try {
  const login = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: USERNAME, password: PASSWORD },
  });
  token = login.access_token;
  const fixtures = await prepareFixtures(token);
  originalSettings = fixtures.originalSettings;
  ok("API 備妥會員、購物金與兩件一般商品", true, fixtures.product.sku);

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on("pageerror", (error) => ok("頁面沒有未捕捉例外", false, String(error)));

  await loginBrowser(page);
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.fill('input[name="code"]', fixtures.product.sku);
  await page.press('input[name="code"]', "Enter");
  await page.getByText(fixtures.product.name, { exact: true }).waitFor();
  await page.getByLabel(`${fixtures.product.name} 數量`).fill("2");
  await page.locator(".pos-total", { hasText: "$400" }).waitFor();

  await page.locator(".pos-member-search input").fill(fixtures.member.name);
  await page.click('button:has-text("查詢會員")');
  await page
    .locator(".pos-member-results button", { hasText: fixtures.member.name })
    .first()
    .click();
  await page.locator(".pos-member-selected", { hasText: "$500" }).waitFor();

  ok(
    "POS 不提供現金＋LINE Pay 多外部付款",
    (await page.getByText("現金＋LINE Pay", { exact: true }).count()) === 0,
  );
  await page.locator(".pos-tender-mode", { hasText: "購物金＋其他付款" }).click();
  await page.locator('label:has-text("本次使用購物金") input').fill("300");
  await page.locator(".pos-mixed-method", { hasText: "LINE Pay" }).click();
  const linePaySplit = page.locator('[aria-label="付款金額拆分"]');
  ok(
    "桌機 POS 顯示購物金 $300＋LINE Pay $100",
    (await linePaySplit.getByText(/購物金\s*\$300/).isVisible()) &&
      (await page
        .locator(".pos-tender .hint", { hasText: "LINE Pay 收款 $100" })
        .isVisible()) &&
      (await page.locator('input[name="linepay_one_time_key"]').isVisible()),
  );
  await page.screenshot({
    path: resolve(SHOTS, "01a-desktop-pos-store-credit-linepay.png"),
    fullPage: true,
  });

  await page.locator(".pos-mixed-method", { hasText: "台灣Pay" }).click();
  await page.locator('label:has-text("已於台灣Pay收到") input').check();
  const split = page.locator('[aria-label="付款金額拆分"]');
  ok("桌機 POS 顯示購物金 $300", await split.getByText(/購物金\s*\$300/).isVisible());
  ok("桌機 POS 顯示台灣Pay 剩餘 $100", await split.getByText(/剩餘應付\s*\$100/).isVisible());
  await page.screenshot({ path: resolve(SHOTS, "01-desktop-pos-mixed.png"), fullPage: true });

  const saleResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/sales") &&
      response.request().method() === "POST" &&
      response.ok(),
  );
  await page.locator("button.pos-checkout").click();
  const sale = await (await saleResponsePromise).json();
  await page.locator(".pos-complete", { hasText: `#${sale.id}` }).waitFor();
  const saleTenders = tenderMap(sale.tenders);
  ok(
    "POS 真實成立購物金 $300＋台灣Pay $100",
    Number(sale.total) === 400 &&
      saleTenders.STORE_CREDIT === 300 &&
      saleTenders.TAIWAN_PAY === 100,
    `sale=${sale.id}`,
  );
  await page.screenshot({ path: resolve(SHOTS, "02-desktop-pos-complete.png"), fullPage: true });

  const printRequestPromise = page.waitForRequest(
    (request) => request.url().endsWith("/print/detail") && request.method() === "POST",
  );
  await page
    .locator('[role="dialog"][aria-label="列印商品明細"] button', { hasText: "列印明細" })
    .click();
  const printPayload = (await printRequestPromise).postDataJSON();
  await page.getByText("已送出列印。").waitFor({ timeout: 8_000 });
  const printTenders = tenderMap(printPayload.tenders);
  ok(
    "列印明細 payload 含交易編號與付款拆分",
    printPayload.id === sale.id &&
      printTenders.STORE_CREDIT === 300 &&
      printTenders.TAIWAN_PAY === 100,
    `交易編號 #${printPayload.id}`,
  );

  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  let dialog = await openReturnDialog(page, sale.id);
  await dialog.getByLabel(`${fixtures.product.name} 退貨數量`).fill("1");
  await dialog.locator('input[placeholder*="尺寸不合"]').fill("第一次部分退貨");
  const firstPreview = dialog.locator('[aria-label="預估退款去向"]');
  ok("第一次退 $200 全數回補購物金", await firstPreview.getByText(/購物金\s*\$200/).isVisible());
  ok("第一次退款不要求台灣Pay 操作", (await dialog.getByText(/已於台灣Pay完成退款/).count()) === 0);
  await page.screenshot({ path: resolve(SHOTS, "03-desktop-first-return.png"), fullPage: true });

  const firstReturnPromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/returns") &&
      response.request().method() === "POST" &&
      response.ok(),
  );
  await dialog.getByRole("button", { name: /確認退貨/ }).click();
  const firstReturn = await (await firstReturnPromise).json();
  await page.getByText(new RegExp(`銷售 #${sale.id} 退貨完成`)).waitFor();
  const firstTenders = tenderMap(firstReturn.refund_tenders);
  ok(
    "第一次實際退款明細＝購物金 $200",
    firstTenders.STORE_CREDIT === 200 && Object.keys(firstTenders).length === 1,
  );

  dialog = await openReturnDialog(page, sale.id);
  await dialog.getByRole("button", { name: "整筆退貨" }).click();
  await dialog.locator('input[placeholder*="尺寸不合"]').fill("第二次退回剩餘商品");
  const secondPreview = dialog.locator('[aria-label="預估退款去向"]');
  ok("第二次預估回補購物金 $100", await secondPreview.getByText(/購物金\s*\$100/).isVisible());
  ok("第二次預估台灣Pay 退款 $100", await secondPreview.getByText(/台灣Pay\s*\$100/).isVisible());
  const secondConfirm = dialog.getByRole("button", { name: /確認退貨/ });
  ok("台灣Pay 未勾人工退款確認時禁止送出", await secondConfirm.isDisabled());

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(SHOTS, "04-mobile-second-return.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: resolve(SHOTS, "05-desktop-second-return.png"), fullPage: true });
  await dialog.locator('label:has-text("已於台灣Pay完成退款") input').check();
  ok("勾選台灣Pay 已退款後可送出", !(await secondConfirm.isDisabled()));

  const secondReturnPromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/returns") &&
      response.request().method() === "POST" &&
      response.ok(),
  );
  await secondConfirm.click();
  const secondReturn = await (await secondReturnPromise).json();
  await page.getByText(new RegExp(`銷售 #${sale.id} 退貨完成`)).waitFor();
  const secondTenders = tenderMap(secondReturn.refund_tenders);
  ok(
    "第二次實際退款明細＝購物金 $100＋台灣Pay $100",
    secondTenders.STORE_CREDIT === 100 && secondTenders.TAIWAN_PAY === 100,
  );

  const finalSale = await apiJson(`/api/v1/sales/${sale.id}`, { token });
  const finalCredit = await apiJson(`/api/v1/contacts/${fixtures.member.id}/store-credit`, {
    token,
  });
  ok("全退後銷售狀態為已退貨", finalSale.status === "RETURNED", finalSale.status);
  ok("兩次退貨後購物金回到 $500", Number(finalCredit.balance) === 500, finalCredit.balance);
  await page.screenshot({ path: resolve(SHOTS, "06-desktop-return-complete.png"), fullPage: true });
} catch (error) {
  ok("流程中斷", false, String(error));
  if (browser) {
    const page = browser.contexts().flatMap((context) => context.pages())[0];
    if (page) {
      await page
        .screenshot({ path: resolve(SHOTS, "99-failure.png"), fullPage: true })
        .catch(() => {});
    }
  }
} finally {
  if (token && originalSettings) {
    await apiJson("/api/v1/settings", {
      method: "PATCH",
      token,
      body: {
        einvoice_enabled: originalSettings.einvoice_enabled,
        require_store_credit_signing: originalSettings.require_store_credit_signing,
        store_credit_min_spend: originalSettings.store_credit_min_spend,
        linepay_enabled: originalSettings.linepay_enabled,
        linepay_fee_pct: originalSettings.linepay_fee_pct,
      },
    }).catch((error) => ok("還原測試前設定", false, String(error)));
  }
  if (browser) await browser.close();
}

const failed = checks.filter((check) => !check.pass);
console.log(`\n結果：${checks.length - failed.length}/${checks.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
