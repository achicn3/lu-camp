// 交易紀錄/作廢 + 開錢櫃煙霧：API 備妥有庫存一般商品 → /pos 掃 SKU 現金結帳（此時應踢開錢櫃，
// 由 agent access log 佐證）→ /sales 找到該筆 → 店長作廢（二次確認）→ 列表顯示已作廢。
// 執行：node scripts/sales-void-smoke.mjs（backend:8000 + frontend:3000 + agent:8001 已起，docs/20）
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = strip(process.env.SMOKE_BASE ?? "http://localhost:3000");
const API_BASE = strip(process.env.SMOKE_API_BASE ?? "http://localhost:8000");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "sales-void");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}
function strip(v) {
  return v.replace(/\/+$/, "");
}

async function apiJson(
  path,
  { method = "GET", token = null, body, expected = [200], headers = {} } = {},
) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!expected.includes(res.status)) {
    throw new Error(`${method} ${path} → ${res.status}: ${JSON.stringify(data?.detail ?? data)}`);
  }
  return data;
}

let browser;
try {
  // ── API fixture：登入、開帳、上架＋進貨一件一般商品 ──
  const login = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: USERNAME, password: PASSWORD },
  });
  const token = login.access_token;
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current === null) {
    await apiJson("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
      expected: [201],
    });
  }
  const runId = `${Date.now()}-${randomUUID().slice(0, 4)}`;
  const product = await apiJson("/api/v1/catalog-products", {
    method: "POST",
    token,
    body: { sku: `VOID-${runId}`, name: `作廢測試品 ${runId}`, unit_price: "300" },
    expected: [201],
  });
  const supplier = await apiJson("/api/v1/suppliers", {
    method: "POST",
    token,
    body: { name: `作廢測試供應商 ${runId}` },
    expected: [201],
  });
  const po = await apiJson("/api/v1/purchase-orders", {
    method: "POST",
    token,
    body: {
      supplier_id: supplier.id,
      lines: [{ catalog_product_id: product.id, qty: 3, unit_cost: "100" }],
      submit: true,
    },
    expected: [201],
  });
  await apiJson(`/api/v1/purchase-orders/${po.id}/receive`, {
    method: "POST",
    token,
    body: { lines: [{ line_id: po.lines[0].id, qty: po.lines[0].qty }] },
    headers: { "Idempotency-Key": `void-recv-${runId}` },
    expected: [200, 201],
  });
  ok("API 測試資料準備完成", true, product.sku);

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

  // ── 登入 ──
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功（MANAGER）", true);

  // ── /pos 現金結帳（此時應踢開錢櫃，見 agent access log）──
  await page.click('a:has-text("POS 結帳")');
  await page.waitForURL(`${BASE}/pos`);
  await page.waitForSelector("text=掃描或輸入商品條碼");
  await page.fill('input[name="code"]', product.sku);
  await page.press('input[name="code"]', "Enter");
  await page.waitForSelector(`text=${product.name}`);
  await page.click('button:has-text("結帳")');
  await page.waitForSelector('h2:has-text("已完成")');
  // 開錢櫃失敗提示「不應」出現（agent 在線 → 踢櫃成功）。
  const drawerWarning = await page.locator("text=錢櫃未開啟").count();
  ok("現金結帳完成且無錢櫃警示（踢櫃成功）", drawerWarning === 0);
  const saleIdText = await page.locator(".pos-complete .badge-open").textContent();
  const saleId = saleIdText?.replace("#", "").trim() ?? "";
  await page.screenshot({ path: `${SHOTS}/01-pos-cash-complete.png` });
  await page.click('button:has-text("不用，完成")').catch(() => {});

  // ── /sales 交易紀錄 → 作廢 ──
  await page.click('a:has-text("交易紀錄")');
  await page.waitForURL(`${BASE}/sales`);
  await page.waitForSelector(`td:has-text("#${saleId}")`);
  ok("交易紀錄列出今日銷售", true, `#${saleId}`);
  await page.screenshot({ path: `${SHOTS}/02-sales-list.png` });

  await page.click(`button[aria-label="作廢銷售 ${saleId}"]`);
  const dialog = page.locator('[role="dialog"][aria-label="作廢銷售確認"]');
  await dialog.waitFor({ state: "visible" });
  ok("作廢二次確認對話框", true);
  await page.screenshot({ path: `${SHOTS}/03-void-confirm.png` });

  await dialog.locator('button:has-text("確認作廢")').click();
  await page.waitForSelector(`text=銷售 #${saleId} 已作廢`);
  const rowVoided = await page
    .locator(`tr:has(td:has-text("#${saleId}")) td:has-text("已作廢")`)
    .count();
  ok("作廢成功、列表顯示已作廢", rowVoided > 0);
  await page.screenshot({ path: `${SHOTS}/04-voided.png` });

  // ── 後端驗證：庫存回補（3 − 1 售出 + 1 作廢回補 = 3）──
  const after = await apiJson(
    `/api/v1/catalog-products/by-sku/${encodeURIComponent(product.sku)}`,
    { token },
  );
  ok("作廢後庫存回補", after.quantity_on_hand === 3, `on_hand=${after.quantity_on_hand}`);
} catch (error) {
  ok("流程中斷", false, String(error));
  if (browser) {
    const page = browser.contexts().flatMap((c) => c.pages())[0];
    if (page) await page.screenshot({ path: `${SHOTS}/99-failure.png` });
  }
} finally {
  if (browser) await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n結果：${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length ? 1 : 0);
