// 採購單事後修改＋進項發票獨立登錄 瀏覽器煙霧（docs/70）：
// ① 品項很多的採購單，收貨視窗捲得到「確認收貨」；
// ② 管理者修改已收貨的單：選錯商品換成對的（庫存跟著移）、品名改錯字；
// ③ 兩張採購單的收貨合併登錄成一張發票；明細頁連到發票；管理者更正發票金額。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_ALLOW_WRITE=1 SMOKE_BASE=http://localhost:3500 SMOKE_API_BASE=http://localhost:8114 \
//   node scripts/purchasing-edit-invoices-smoke.mjs
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立商品、採購單與發票，請指向隔離測試庫");
const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "purchasing-edit-invoices");
const RUN = String(Date.now()).slice(-6);
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

let token = "";
async function api(method, path, body, headers = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  return response.status === 204 ? null : response.json();
}

let productSeq = 0;
async function product(name) {
  productSeq += 1;
  // 冪等鍵只能是 ASCII（HTTP 標頭），不能放中文品名。
  return api(
    "POST",
    "/api/v1/catalog-products",
    { sku: null, name, unit_price: 150, reorder_point: 0 },
    { "Idempotency-Key": `pe-${RUN}-${productSeq}` },
  );
}

async function receivedOrder(supplierId, lines) {
  const po = await api("POST", "/api/v1/purchase-orders", {
    supplier_id: supplierId,
    lines: lines.map(({ id, qty, cost }) => ({ catalog_product_id: id, qty, unit_cost: String(cost) })),
    submit: true,
  });
  const received = await api(
    "POST",
    `/api/v1/purchase-orders/${po.id}/receive`,
    { lines: po.lines.map((l) => ({ line_id: l.id, qty: l.qty })) },
    { "Idempotency-Key": `pe-recv-${RUN}-${po.id}` },
  );
  return { po, receiptId: received.receipt_id };
}

async function stock(id) {
  return (await api("GET", `/api/v1/catalog-products/${id}`)).quantity_on_hand;
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  ({ access_token: token } = await (
    await fetch(`${API}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "dev-manager", password: "dev-test-123456" }),
    })
  ).json());
  const supplier = await api("POST", "/api/v1/suppliers", {
    name: `月結廠商-${RUN}`,
    contact: null,
    tax_id: null,
  });

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));

  // ① 很長的採購單：收貨視窗要捲得到確認鈕
  const many = [];
  for (let i = 0; i < 30; i += 1) many.push(await product(`長單品-${RUN}-${i}`));
  const longPo = await api("POST", "/api/v1/purchase-orders", {
    supplier_id: supplier.id,
    lines: many.map((p) => ({ catalog_product_id: p.id, qty: 2, unit_cost: "50" })),
    submit: true,
  });
  await page.goto(`${BASE}/purchasing/${longPo.id}`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "收貨入庫" }).click();
  const confirm = page.getByRole("button", { name: "確認收貨" });
  await confirm.scrollIntoViewIfNeeded();
  const reachable = await confirm.evaluate((el) => {
    const r = el.getBoundingClientRect();
    return r.top >= 0 && r.bottom <= window.innerHeight;
  });
  ok("30 項的採購單：收貨視窗的「確認收貨」在畫面內按得到", reachable);
  await page.screenshot({ path: join(SHOTS, "01-long-receive-dialog.png") });
  await confirm.click();
  await page.locator("span.inv-badge", { hasText: "已收貨" }).waitFor();
  ok("長單收貨完成", true);

  // ② 選錯商品＋品名錯字：管理者修改已收貨的單
  const wrong = await product(`選錯的帳篷-${RUN}`);
  const right = await product(`正確的帳蓬-${RUN}`);
  const { po: swapPo } = await receivedOrder(supplier.id, [{ id: wrong.id, qty: 4, cost: 800 }]);
  const before = { wrong: await stock(wrong.id), right: await stock(right.id) };
  await page.goto(`${BASE}/purchasing/${swapPo.id}`, { waitUntil: "networkidle" });
  await page.getByRole("link", { name: "修改" }).click();
  await page.waitForURL(new RegExp(`/purchasing/${swapPo.id}/edit$`));
  await page.getByLabel(`已收 ${wrong.name}`).waitFor();
  ok("修改頁帶入原明細與「已收」欄", (await page.getByLabel(`已收 ${wrong.name}`).inputValue()) === "4");
  await page.getByRole("button", { name: `移除 ${wrong.name}` }).click();
  await page.getByLabel("搜尋一般商品").fill(right.name);
  await page.getByRole("button", { name: new RegExp(`＋ ${right.name}`) }).click();
  await page.getByLabel(`數量 ${right.name}`).fill("4");
  await page.getByLabel(`已收 ${right.name}`).fill("4");
  await page.getByLabel(`進貨單價 ${right.name}`).fill("800");
  await page.getByRole("button", { name: `改名 ${right.name}` }).click();
  const fixedName = `正確的帳篷-${RUN}`;
  await page.getByLabel(`新品名 ${right.name}`).fill(fixedName);
  await page.getByRole("button", { name: "儲存品名" }).click();
  await page.getByRole("button", { name: `改名 ${fixedName}` }).waitFor();
  ok("品名錯字直接在修改頁改好", true, fixedName);
  await page.screenshot({ path: join(SHOTS, "02-edit-received-order.png"), fullPage: true });
  await page.getByRole("button", { name: "儲存修改" }).click();
  await page.waitForURL(new RegExp(`/purchasing/${swapPo.id}$`));
  await page.getByText(fixedName, { exact: true }).waitFor();
  const after = { wrong: await stock(wrong.id), right: await stock(right.id) };
  ok(
    "換商品後庫存跟著移：錯的扣回 4、對的加 4",
    after.wrong === before.wrong - 4 && after.right === before.right + 4,
    JSON.stringify({ before, after }),
  );
  await page.screenshot({ path: join(SHOTS, "03-detail-after-edit.png"), fullPage: true });

  // ③ 兩張採購單的收貨合併成一張發票（整月合併開）
  const first = await receivedOrder(supplier.id, [{ id: right.id, qty: 2, cost: 300 }]);
  const second = await receivedOrder(supplier.id, [{ id: right.id, qty: 1, cost: 400 }]);
  await page.goto(`${BASE}/purchasing/${first.po.id}`, { waitUntil: "networkidle" });
  await page.getByText(/尚未開發票/).waitFor();
  await page.getByRole("link", { name: "登錄發票" }).click();
  await page.waitForURL(/\/purchasing\/invoices\/new\?/);
  const firstBox = page.getByLabel(new RegExp(`採購單 #${first.po.id} .* 收貨`));
  await firstBox.waitFor();
  ok("從收貨批次進來：供應商與那一批已勾好", await firstBox.isChecked());
  const swapRow = page.locator("tr", { hasText: `#${swapPo.id}` });
  ok("改過的採購單，這批金額照改後算（4 × 800）", (await swapRow.innerText()).includes("3,200"));
  await page.getByLabel(new RegExp(`採購單 #${second.po.id} .* 收貨`)).check();
  const number = `QA${RUN.padStart(8, "0").slice(-8)}`;
  await page.getByLabel("發票號碼").fill(number);
  await page.getByLabel("發票日期").fill("2026-10-31");
  await page.getByLabel("發票未稅金額").fill("1000");
  await page.getByLabel("發票稅額").fill("50");
  await page.getByLabel("發票含稅金額").fill("1050");
  const hint = await page.getByRole("status").innerText();
  ok("勾選合計與發票不同只提醒不擋", hint.includes("差"), hint);
  await page.screenshot({ path: join(SHOTS, "04-new-invoice-merged.png"), fullPage: true });
  await page.getByRole("button", { name: "登錄發票" }).click();
  await page.waitForURL(/\/purchasing\/invoices\/\d+$/);
  const invoiceId = Number(new URL(page.url()).pathname.split("/").pop());
  const saved = await api("GET", `/api/v1/purchase-input-invoices/${invoiceId}`);
  ok(
    "一張發票涵蓋兩張採購單的收貨",
    saved.receipts.length === 2 && saved.receipts_total === "1000",
    JSON.stringify(saved.receipts.map((r) => r.purchase_order_id)),
  );

  // 管理者更正發票金額
  await page.getByLabel("發票稅額").fill("52");
  await page.getByLabel("發票含稅金額").fill("1052");
  await page.getByRole("button", { name: "儲存修改" }).click();
  await page.getByText("已儲存。").waitFor();
  const corrected = await api("GET", `/api/v1/purchase-input-invoices/${invoiceId}`);
  ok("管理者更正發票金額", corrected.invoice_total === "1052", corrected.invoice_total);
  await page.screenshot({ path: join(SHOTS, "05-invoice-edit.png"), fullPage: true });

  await page.goto(`${BASE}/purchasing/${second.po.id}`, { waitUntil: "networkidle" });
  const link = page.getByRole("link", { name: number });
  await link.waitFor();
  ok("採購單明細的收貨批次連到那張發票", (await link.getAttribute("href")) === `/purchasing/invoices/${invoiceId}`);

  await page.goto(`${BASE}/purchasing?tab=invoices`, { waitUntil: "networkidle" });
  await page.getByRole("link", { name: number }).waitFor();
  ok("採購頁「進項發票」分頁列出這張發票", true);
  await page.screenshot({ path: join(SHOTS, "06-invoice-list.png"), fullPage: true });

  // 手機寬度：發票表單不橫向捲動
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/purchasing/invoices/new?supplier=${supplier.id}`, { waitUntil: "networkidle" });
  await page.getByLabel("發票號碼").waitFor();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  ok("手機寬度：登錄發票不橫向捲動", overflow <= 0, `溢出 ${overflow}px`);
  await page.screenshot({ path: join(SHOTS, "07-mobile-invoice.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  ok("流程跑完", false, String(error));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} PASS；截圖 ${SHOTS}`);
process.exit(failed === 0 ? 0 : 1);
