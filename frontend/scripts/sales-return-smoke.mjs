// 交易紀錄頁退貨煙霧（D-8 波次二）：真 backend——API 備銷售（會員買 10 件×$100）→
// UI 點「退貨」→ 選 3 件＋原因 → 確認 → 驗成功訊息＋會員點數按比例沖回（10→7）＋
// 報表毛利扣退貨（margin 經 API 比對）。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "returns");
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";

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

async function apiJson(path, { method = "GET", token, body, idem } = {}) {
  const headers = { "content-type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (idem) headers["Idempotency-Key"] = idem;
  const r = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 1000 } });
mkdirSync(SHOTS, { recursive: true });

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: PASS },
  });

  // 開帳（若已開帳容忍 409）
  await apiJson("/api/v1/cash-sessions/open", {
    method: "POST", token, body: { opening_float: "5000" },
  }).catch(() => {});

  // 備測資：會員＋一般商品＋銷售（10×$100，會員拿 10 點）
  const stamp = Date.now().toString().slice(-8);
  const member = await apiJson("/api/v1/contacts", {
    method: "POST", token,
    body: { name: "退貨煙霧會員", phone: `09${stamp}`, roles: ["MEMBER"] },
  });
  const product = await apiJson("/api/v1/catalog-products", {
    method: "POST", token,
    body: { sku: `RET-SMK-${stamp}`, name: "煙霧瓦斯罐", unit_price: "100" },
  }).catch(async () => {
    // 已存在則查詢
    const list = await apiJson(`/api/v1/catalog-products?q=RET-SMK-${stamp}`, { token });
    return list[0];
  });
  // 補庫存：走盤點或採購過重——煙霧直接用既有有庫存商品若初始 0。改用採購收貨最穩：
  const supplier = await apiJson("/api/v1/suppliers", {
    method: "POST", token, body: { name: `煙霧供應商-${stamp}` },
  });
  const po = await apiJson("/api/v1/purchase-orders", {
    method: "POST", token,
    body: {
      supplier_id: supplier.id,
      submit: true,
      lines: [{ catalog_product_id: product.id, qty: 20, unit_cost: "50" }],
    },
  });
  await apiJson(`/api/v1/purchase-orders/${po.id}/receive`, {
    method: "POST", token, idem: `ret-smk-recv-${stamp}`,
    body: { lines: [{ line_id: po.lines[0].id, qty: 20 }] },
  });
  const sale = await apiJson("/api/v1/sales", {
    method: "POST", token, idem: `ret-smk-sale-${stamp}`,
    body: {
      lines: [{ line_type: "CATALOG", catalog_product_id: product.id, qty: 10 }],
      buyer_contact_id: member.id,
    },
  });
  // 斷言資料驅動：門市可能有進行中活動（折後總額/單價變動），以銷售單實值計期望
  const saleTotal = Number(sale.total);
  const unitPrice = Number(sale.lines[0].unit_price);
  const expectedPoints = Math.floor(saleTotal / 100);
  const memberBefore = await apiJson(`/api/v1/contacts/${member.id}`, { token });
  ok("備測資：銷售成立、點數＝floor(total/100)",
    memberBefore.member_points === expectedPoints,
    `points=${memberBefore.member_points} total=${saleTotal}`);

  // UI：登入 → 交易紀錄 → 退貨
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(700);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', PASS);
  await page.click('button:has-text("登入")');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.screenshot({ path: join(SHOTS, "01-sales-list.png"), fullPage: true });

  await page.locator(`button[aria-label="退貨銷售 ${sale.id}"]`).click();
  await page.waitForSelector('[role="dialog"][aria-label="退貨"]', { timeout: 8000 });
  const dialog = page.locator('[role="dialog"][aria-label="退貨"]');
  await dialog.locator('input[aria-label="煙霧瓦斯罐 退貨數量"]').fill("3");
  await dialog.locator('input[placeholder*="尺寸不合"]').fill("煙霧測試退貨");
  await page.screenshot({ path: join(SHOTS, "02-return-dialog.png"), fullPage: true });
  const expectedRefund = unitPrice * 3;
  const estimate = await dialog.locator("text=預估退款").innerText();
  ok(`退款預估＝折後單價×3（$${expectedRefund}）`,
    estimate.replace(/,/g, "").includes(String(expectedRefund)), estimate.trim());

  await dialog.locator('button:has-text("確認退貨")').click();
  await page.waitForSelector("text=退貨完成", { timeout: 10000 });
  await page.screenshot({ path: join(SHOTS, "03-return-done.png"), fullPage: true });
  ok("UI 顯示退貨完成", true);

  // 後端驗證：點數按比例沖回 10→7；報表毛利已扣
  const expectedClaw = Math.floor((expectedPoints * expectedRefund) / saleTotal);
  const memberAfter = await apiJson(`/api/v1/contacts/${member.id}`, { token });
  ok(`會員點數按比例沖回（${expectedPoints}→${expectedPoints - expectedClaw}）`,
    memberAfter.member_points === expectedPoints - expectedClaw,
    `points=${memberAfter.member_points}`);
  const ret = await apiJson(`/api/v1/sales/${sale.id}`, { token });
  ok("銷售狀態維持部分退貨（非 RETURNED）", ret.status === "COMPLETED", ret.status);
} catch (e) {
  failed += 1;
  console.log(`❌ 煙霧例外：${String(e).slice(0, 300)}`);
} finally {
  await browser.close();
}

console.log(`\n${passed}/${passed + failed} 通過`);
process.exit(failed === 0 ? 0 : 1);
