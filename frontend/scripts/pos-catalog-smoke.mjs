// POS 數量型商品掃碼售出煙霧：API 準備（上架 SKU → 採購單 → 收貨補庫存）→ 登入 → /pos
// → 掃無庫存 SKU 阻擋 → 掃可售 SKU 加入購物車 → 調數量（含超量截頂）→ 現金結帳完成。
// 執行：node scripts/pos-catalog-smoke.mjs（需 backend:8000 + frontend:3000 已起，見 docs/20）
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = stripTrailingSlash(process.env.SMOKE_BASE ?? "http://localhost:3000");
const API_BASE = stripTrailingSlash(process.env.SMOKE_API_BASE ?? "http://localhost:8000");
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "pos-catalog");
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

function money(amount) {
  return Number(amount).toLocaleString("en-US", { maximumFractionDigits: 0 });
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
      data && typeof data === "object" && "detail" in data ? JSON.stringify(data.detail) : text;
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

// 上架數量品（初始庫存 0）→ 建採購單 → 收貨補庫存（依實際流程，不繞道直改 DB）。
async function createStockedCatalogProduct(token, runId, { qty, unitPrice, unitCost }) {
  const product = await apiJson("/api/v1/catalog-products", {
    method: "POST",
    token,
    body: {
      sku: `SMOKE-GAS-${runId}`,
      name: `煙霧瓦斯罐 ${runId}`,
      unit_price: String(unitPrice),
    },
    expected: [201],
  });
  const supplier = await apiJson("/api/v1/suppliers", {
    method: "POST",
    token,
    body: { name: `煙霧供應商 ${runId}` },
    expected: [201],
  });
  const po = await apiJson("/api/v1/purchase-orders", {
    method: "POST",
    token,
    body: {
      supplier_id: supplier.id,
      lines: [{ catalog_product_id: product.id, qty, unit_cost: String(unitCost) }],
    },
    expected: [201],
  });
  await apiJson(`/api/v1/purchase-orders/${po.id}/receive`, {
    method: "POST",
    token,
    expected: [200, 201],
  });
  const stocked = await apiJson(
    `/api/v1/catalog-products/by-sku/${encodeURIComponent(product.sku)}`,
    { token },
  );
  if (stocked.quantity_on_hand !== qty) {
    throw new Error(`收貨後庫存應為 ${qty}，得到 ${stocked.quantity_on_hand}`);
  }
  return stocked;
}

async function createEmptyCatalogProduct(token, runId) {
  return await apiJson("/api/v1/catalog-products", {
    method: "POST",
    token,
    body: {
      sku: `SMOKE-EMPTY-${runId}`,
      name: `煙霧缺貨品 ${runId}`,
      unit_price: "50",
    },
    expected: [201],
  });
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

async function expectTotal(page, amount, label) {
  await page.waitForFunction(
    (expected) => document.querySelector(".pos-total strong")?.textContent?.includes(expected),
    money(amount),
  );
  const total = await page.locator(".pos-total strong").textContent();
  ok(label, total?.includes(money(amount)) ?? false, total ?? "");
}

let browser;
try {
  const token = await loginApi();
  await ensureOpenCashSession(token);
  const runId = `${Date.now()}-${randomUUID().slice(0, 6)}`;
  const product = await createStockedCatalogProduct(token, runId, {
    qty: 5,
    unitPrice: 120,
    unitCost: 60,
  });
  const emptyProduct = await createEmptyCatalogProduct(token, runId);
  ok("API 測試資料準備完成", true, `${product.sku} 庫存 ${product.quantity_on_hand}`);

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

  await loginBrowser(page);
  await page.click('a:has-text("POS 結帳")');
  await page.waitForURL(`${BASE}/pos`);
  await page.waitForSelector("text=掃描或輸入商品條碼");

  // 1) 無庫存 SKU 阻擋
  await scanCode(page, emptyProduct.sku);
  const blocked = page.locator('[role="alert"]', { hasText: "已無庫存" });
  await blocked.waitFor({ state: "visible" });
  ok("無庫存數量品掃碼阻擋", true, (await blocked.textContent()) ?? "");
  await page.screenshot({ path: `${SHOTS}/01-catalog-empty-blocked.png` });

  // 2) 掃可售 SKU 加入購物車（序號/散裝 404 → fallback 數量品）
  await scanCode(page, product.sku);
  await page.waitForSelector(`text=${product.name}`);
  ok("掃 SKU 加入數量品", true, product.sku);
  await expectTotal(page, 120, "單件總額正確");

  // 3) 調數量 2 → 總額 240；超量 99 → 截頂庫存 5 → 600
  const qtyInput = page.getByLabel(`${product.name} 數量`);
  await qtyInput.fill("2");
  await expectTotal(page, 240, "數量 2 總額正確");
  await qtyInput.fill("99");
  await expectTotal(page, 600, "超量截頂為庫存 5（總額 600）");
  await qtyInput.fill("2");
  await expectTotal(page, 240, "回調數量 2");
  await page.screenshot({ path: `${SHOTS}/02-catalog-in-cart.png` });

  // 4) 現金結帳完成
  await page.click('button:has-text("結帳")');
  await page.waitForSelector("text=已完成");
  ok("數量品現金結帳完成", true);
  await page.screenshot({ path: `${SHOTS}/03-catalog-cash-complete.png` });

  // 5) 後端庫存已扣（5 − 2 = 3）
  const after = await apiJson(
    `/api/v1/catalog-products/by-sku/${encodeURIComponent(product.sku)}`,
    { token },
  );
  ok("售出後庫存扣減", after.quantity_on_hand === 3, `on_hand=${after.quantity_on_hand}`);
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
