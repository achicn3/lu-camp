// 採購/補貨瀏覽器煙霧（2026-09-23 改版）：自行用 API 造資料 →
// 列表頁（滿版、低庫存提示、翻頁）→ 低庫存「全部帶入」進建立頁（數量補到補貨點）→
// 新增商品（品牌→型號→品名自動帶入、建議售價自動算、不出現 SKU）→ 送出 → 明細頁 →
// 收貨入庫 → 已收貨＋可印標籤 → 回列表看到已收貨。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/purchasing-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "purchasing");
const RUN = String(Date.now()).slice(-5);
const LOW = `高山瓦斯罐-${RUN}`;
const SUPPLIER = `山林供應商-${RUN}`;
const BRAND = `SnowPeak-${RUN}`;
const MODEL = `GST-${RUN}`;
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(path, { method = "GET", token, body, headers = {} } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  return response.json();
}

async function seed(token) {
  const supplier = await apiJson("/api/v1/suppliers", {
    method: "POST",
    token,
    body: { name: SUPPLIER, contact: null, tax_id: null },
  });
  const low = await apiJson("/api/v1/catalog-products", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": `smoke-low-${RUN}` },
    body: { sku: null, name: LOW, unit_price: 150, reorder_point: 5 },
  });
  // 造 21 張草稿，讓列表出現第二頁（每頁 20）。
  for (let i = 0; i < 21; i += 1) {
    await apiJson("/api/v1/purchase-orders", {
      method: "POST",
      token,
      body: {
        supplier_id: supplier.id,
        lines: [{ catalog_product_id: low.id, qty: 1, unit_cost: "90" }],
        submit: false,
      },
    });
  }
  // 草稿不算在途；把它們取消，免得低庫存「在途」判斷被干擾。
  const drafts = await apiJson(`/api/v1/purchase-orders?status=DRAFT&limit=50`, { token });
  for (const po of drafts.filter((p) => p.supplier_id === supplier.id)) {
    await apiJson(`/api/v1/purchase-orders/${po.id}/cancel`, { method: "POST", token });
  }
  return { supplier, low };
}

async function pickCombo(page, label, text, { create = false } = {}) {
  const input = page.getByLabel(label, { exact: true });
  await input.click();
  await input.fill(text);
  if (create) {
    await page.click(`button:has-text("建立「${text}」")`);
  } else {
    await page.getByRole("option", { name: text, exact: true }).click();
  }
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  const { low } = await seed(token);
  ok("API 造資料（供應商、低庫存商品、21 張已取消的採購單）", true);

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  // 1) 列表頁
  await page.goto(`${BASE}/purchasing`, { waitUntil: "networkidle" });
  await page.getByRole("region", { name: "低庫存提醒" }).waitFor();
  ok("列表頁頂端有低庫存提示", true);
  ok("右上角有建立採購單", await page.locator(".pur-page-head").getByRole("link", { name: "＋ 建立採購單" }).isVisible());
  await page.getByRole("button", { name: "全部", exact: true }).click();
  await page.getByText(/第 1 \/ \d+ 頁/).waitFor();
  ok("全部採購單可翻頁", true, await page.getByText(/第 1 \/ \d+ 頁/).innerText());
  await page.screenshot({ path: join(SHOTS, "01-list.png"), fullPage: true });

  // 2) 低庫存全部帶入 → 建立頁
  await page.getByRole("link", { name: "全部帶入建立採購單" }).click();
  await page.waitForURL(/\/purchasing\/new\?reorder=/);
  const lowQty = page.getByLabel(`數量 ${LOW}`);
  await lowQty.waitFor();
  ok("低庫存商品已帶入、數量補到補貨點", (await lowQty.inputValue()) === "5", await lowQty.inputValue());
  await pickCombo(page, "供應商", SUPPLIER);
  // 同一個資料庫重跑時，前幾輪造的低庫存商品也會被帶進來：一律填單價，免得送出鈕停用。
  for (const input of await page.locator('.pur-lines input[aria-label^="進貨單價"]').all()) {
    await input.fill("100");
  }

  // 3) 新增商品：品牌→型號→品名自動帶入，建議售價自動算
  await page.getByRole("button", { name: "＋ 新增商品" }).click();
  ok("新增商品沒有 SKU 欄位", (await page.getByLabel("一般商品編號").count()) === 0);
  await pickCombo(page, "品牌", BRAND, { create: true });
  await pickCombo(page, "型號", MODEL, { create: true });
  await page
    .waitForFunction(
      (model) => document.querySelector('input[aria-label="一般商品名稱"]')?.value === model,
      MODEL,
      { timeout: 5000 },
    )
    .catch(() => {});
  const nameValue = await page.getByLabel("一般商品名稱").inputValue();
  ok("品名自動等於型號", nameValue === MODEL, nameValue);
  await page.getByLabel("一般商品進貨成本").fill("500");
  await page.getByLabel("一般商品採購數量").fill("6");
  const price = page.getByLabel("一般商品售價");
  await page.waitForFunction(
    () => Number(document.querySelector('input[aria-label="一般商品售價"]')?.value) > 0,
  );
  const priceValue = Number(await price.inputValue());
  ok("建議售價自動算出並進位到 10 元", priceValue > 500 && priceValue % 10 === 0, String(priceValue));
  await page.screenshot({ path: join(SHOTS, "02-new-product.png"), fullPage: true });
  await page.getByRole("button", { name: "建立並加入採購單" }).click();
  await page.getByLabel(`進貨單價 ${MODEL}`).waitFor();
  ok("新商品帶著成本與數量加入明細", (await page.getByLabel(`數量 ${MODEL}`).inputValue()) === "6");
  await page.screenshot({ path: join(SHOTS, "03-new-order.png"), fullPage: true });

  // 4) 送出 → 明細頁
  await page.getByRole("button", { name: "送出採購" }).click();
  await page.waitForURL(/\/purchasing\/\d+$/);
  await page.getByText(/採購單 #\d+/).waitFor();
  ok("送出後進到明細頁", true, page.url());
  await page.screenshot({ path: join(SHOTS, "04-detail-ordered.png"), fullPage: true });

  // 5) 收貨入庫
  await page.getByRole("button", { name: "收貨入庫" }).click();
  await page.getByRole("dialog", { name: "確認收貨" }).waitFor();
  await page.screenshot({ path: join(SHOTS, "05-receive-dialog.png"), fullPage: true });
  await page.getByRole("button", { name: "確認收貨" }).click();
  await page.locator("span.inv-badge", { hasText: "已收貨" }).waitFor();
  ok("收貨完成、狀態已收貨", true);
  ok(
    "收到的商品可直接印標籤",
    (await page.getByRole("button", { name: `印標籤 ${MODEL}` }).count()) === 1 &&
      (await page.getByRole("button", { name: `印標籤 ${LOW}` }).count()) === 1,
  );
  await page.screenshot({ path: join(SHOTS, "06-detail-received.png"), fullPage: true });

  // 6) 回列表
  await page.getByRole("link", { name: "← 回採購單列表" }).click();
  await page.waitForURL(`${BASE}/purchasing`);
  await page.getByRole("button", { name: "已收貨", exact: true }).click();
  await page.locator("tr", { hasText: SUPPLIER }).first().waitFor();
  ok("列表看得到剛收貨的採購單", true);
  const stock = await apiJson(`/api/v1/catalog-products/${low.id}`, { token });
  ok("低庫存商品已入庫", stock.quantity_on_hand === 5, String(stock.quantity_on_hand));

  // 7) 手機寬度
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/purchasing/new`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "＋ 新增商品" }).click();
  await page.screenshot({ path: join(SHOTS, "07-mobile-new.png"), fullPage: true });
  await page.goto(`${BASE}/purchasing`, { waitUntil: "networkidle" });
  await page.screenshot({ path: join(SHOTS, "08-mobile-list.png"), fullPage: true });
  ok("手機寬度可用", true);

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  ok("流程例外", false, String(error));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exitCode = failed > 0 ? 1 : 0;
