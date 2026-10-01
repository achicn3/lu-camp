// POS 完成畫面：LINE Pay 沒回載具 → 說明「已改印紙本發票」（店主 2026-10-01）。
//
// **本腳本攔截了哪些回應（別誤讀涵蓋範圍）**：真的跑一筆要 LINE Pay 沙盒扣款＋光貿開票，
// 而「沒回載具」取決於 LINE Pay 有沒有開通 merchantReference，沙盒無從控制。所以在瀏覽器端攔截：
//   - GET /settings：假裝 LINE Pay 與電子發票都開著
//   - POST /sales：回一筆 LINE Pay 付款成功的銷售（後端沒有真的扣款、也沒寫入）
//   - POST /einvoice/sales/{id}/issue：回一張沒有載具（或有載具）的發票
//   - 硬體代理列印與 proof-printed：一律當成功
// 驗的是**完成畫面的說明文字**；後端「沒載具就印證明聯」由 tests/integration 守。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/pos-linepay-no-carrier-smoke.mjs
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "pos-linepay-no-carrier");
const RUN = Date.now().toString(36).toUpperCase();
const NO_CARRIER_NOTE = "LINE Pay 沒有回傳客人的載具，已改印紙本發票。";
mkdirSync(SHOTS, { recursive: true });

const results = [];
const ok = (name, pass, detail = "") => {
  results.push(pass);
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
};

async function api(token, method, path, body, extra = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...extra,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: res.status, json: await res.json().catch(() => null) };
}

const token = (
  await api(null, "POST", "/api/v1/auth/login", {
    username: "dev-manager",
    password: "dev-test-123456",
  })
).json.access_token;
const cash = await api(token, "GET", "/api/v1/cash-sessions/current");
if (cash.json === null) await api(token, "POST", "/api/v1/cash-sessions/open", { opening_float: "2000" });

const SKU = `LPNC-${RUN}`;
const NAME = `載具提示測試品-${RUN}`;
const product = await api(token, "POST", "/api/v1/catalog-products", {
  sku: SKU,
  name: NAME,
  unit_price: "500",
});
const supplier = await api(token, "POST", "/api/v1/suppliers", { name: `載具提示供應商-${RUN}` });
const po = await api(token, "POST", "/api/v1/purchase-orders", {
  supplier_id: supplier.json.id,
  submit: true,
  lines: [{ catalog_product_id: product.json.id, qty: 5, unit_cost: "200" }],
});
await api(
  token,
  "POST",
  `/api/v1/purchase-orders/${po.json.id}/receive`,
  { lines: po.json.lines.map((l) => ({ line_id: l.id, qty: l.qty })) },
  { "Idempotency-Key": `lpnc-recv-${randomUUID()}` },
);
ok("前置：商品入庫", product.status === 201, `HTTP ${product.status}`);

let saleSeq = 900000;
function fakeSale() {
  saleSeq += 1;
  return {
    id: saleSeq,
    store_id: 1,
    status: "COMPLETED",
    invoice_status: "PENDING_ISSUE",
    invoice_no: null,
    payment_method: "LINE_PAY",
    service_mode: null,
    table_no: null,
    buyer_contact_id: null,
    buyer_name: null,
    clerk_user_id: 1,
    clerk_name: "dev-manager",
    created_at: new Date().toISOString(),
    subtotal: "500",
    total: "500",
    tax: "24",
    total_discount: "0",
    total_manual_discount: "0",
    gift_retail_value: "0",
    lines: [],
    tenders: [{ id: 1, tender_type: "LINE_PAY", amount: "500", fee_amount: "8" }],
  };
}
function fakeInvoice(saleId, carrierId) {
  return {
    id: saleId,
    store_id: 1,
    sale_id: saleId,
    status: "ISSUED",
    invoice_type: "B2C",
    issue_channel: "AMEGO",
    invoice_no: `LP${String(saleId).slice(-8).padStart(8, "0")}`,
    invoice_date: new Date().toISOString().slice(0, 10),
    invoice_time: "12:00:00",
    random_number: "1234",
    total: "500",
    net: "476",
    tax: "24",
    buyer_tax_id: null,
    buyer_name: null,
    carrier_type: carrierId ? "3J0002" : null,
    carrier_id: carrierId,
    donate_mark: false,
    npoban: null,
    print_mark: carrierId === null,
    // 要印證明聯時光貿會回條碼／QR 內容；給齊才會走「送印」而不是「請到光貿補印」
    barcode_text: carrierId === null ? "11510LP123456781234" : null,
    qrcode_left: carrierId === null ? `LP12345678${"0".repeat(67)}` : null,
    qrcode_right: carrierId === null ? "**" : null,
    created_at: new Date().toISOString(),
  };
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
page.on("pageerror", (err) => ok("頁面無 JS 例外", false, String(err)));
await skipOpeningCheckRedirect(page);

let carrierForNext = null;
await page.route("**/api/v1/settings", async (route) => {
  if (route.request().method() !== "GET") return route.continue();
  const res = await route.fetch();
  const body = await res.json();
  await route.fulfill({ response: res, json: { ...body, linepay_enabled: true, einvoice_enabled: true } });
});
await page.route("**/api/v1/sales", async (route) => {
  if (route.request().method() !== "POST") return route.continue();
  await route.fulfill({ status: 201, json: fakeSale() });
});
await page.route("**/api/v1/einvoice/sales/*/issue", async (route) => {
  const saleId = Number(route.request().url().match(/sales\/(\d+)\/issue/)[1]);
  await route.fulfill({ status: 200, json: fakeInvoice(saleId, carrierForNext) });
});

// 證明聯：假發票的 QR 不是光貿真的加密內容，假機代理會拒印；這裡只驗說明文字，列印一律當成功。
await page.route("**/print/**", (route) =>
  route.request().method() === "POST" ? route.fulfill({ status: 200, json: { ok: true } }) : route.continue(),
);
await page.route("**/api/v1/einvoice/sales/*/proof-printed", (route) =>
  route.fulfill({ status: 204, body: "" }),
);

async function checkoutWithLinePay(shot) {
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.fill('input[name="code"]', SKU);
  await page.press('input[name="code"]', "Enter");
  await page.waitForSelector(`text=${NAME}`);
  await page.locator(".pos-tender-mode", { hasText: "LINE Pay" }).click();
  await page.fill('input[name="linepay_one_time_key"]', "123456789012345678");
  await page.click('button:has-text("結帳")');
  await page.waitForSelector("text=LINE Pay 收款成功", { timeout: 20000 });
  const printDialog = page.locator('[role="dialog"][aria-label="列印商品明細"]');
  if (await printDialog.isVisible().catch(() => false)) {
    await printDialog.getByRole("button", { name: /不用，完成|^完成$/ }).click();
    await printDialog.waitFor({ state: "hidden" });
  }
  await page.locator(".pos-invoice-note").waitFor();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/${shot}` });
  return (await page.locator(".pos-invoice-note").textContent()) ?? "";
}

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  carrierForNext = null;
  const noCarrier = await checkoutWithLinePay("01-linepay-no-carrier.png");
  ok("LINE Pay 沒回載具 → 說明已改印紙本", noCarrier.includes(NO_CARRIER_NOTE), noCarrier);
  ok("沒回載具時不會誤稱存入載具", !noCarrier.includes("綁定的載具"));

  carrierForNext = "/ABC1234";
  const withCarrier = await checkoutWithLinePay("02-linepay-with-carrier.png");
  ok("LINE Pay 有回載具 → 仍顯示存入載具、未列印", withCarrier.includes("綁定的載具 /ABC1234"), withCarrier);
  ok("有載具時不顯示「已改印紙本」", !withCarrier.includes(NO_CARRIER_NOTE));
} catch (err) {
  await page.screenshot({ path: `${SHOTS}/error.png` }).catch(() => {});
  ok("煙霧流程", false, String(err));
}

await browser.close();
const failed = results.filter((p) => !p).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exit(failed === 0 ? 0 : 1);
