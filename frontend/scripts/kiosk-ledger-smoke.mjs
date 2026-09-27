// 顧客螢幕結帳手帳煙霧（店主 2026-09-27：第一人稱露營手帳）：
//   第一件：鏡頭拉近＋桌面滑上來，手寫一行（寫到一半截圖、寫完截圖）
//   連續快速掃描：兩筆幾乎同時進來，動畫不排隊、最後全部清楚顯示
//   同品項數量 1→2：不新增一行，只改數量
//   刪除：該行劃線後收起
//   開始付款 → 付款處理中 → 成交：勾勾一筆畫、印章、背景舉杯（paid）
//   減少動態效果：商品直接顯示，沒有手寫遮罩
// 需 backend + frontend 已起、已 seed（dev-manager、dev-kiosk）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/kiosk-ledger-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "kiosk-ledger");
const RUN = Date.now().toString(36);
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body, extra = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...extra },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

function uniquePhone() {
  return `09${String(Date.now()).slice(-8)}`;
}

function validNationalId() {
  const letters = "ABCDEFGHJKLMNPQRSTUVXYWZIO";
  const code = 10 + Math.floor(Math.random() * 26);
  const digits = [1, ...Array.from({ length: 7 }, () => Math.floor(Math.random() * 10))];
  const weights = [8, 7, 6, 5, 4, 3, 2, 1];
  let sum = Math.floor(code / 10) + (code % 10) * 9;
  digits.forEach((d, i) => (sum += d * weights[i]));
  return `${letters[code - 10]}${digits.join("")}${(10 - (sum % 10)) % 10}`;
}

async function pair(context, mgr) {
  const page = await context.newPage();
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  await page.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-kiosk");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("啟用裝置")');
  await page.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  const code = (await page.textContent(".kiosk-pairing-code"))?.trim();
  const terminal = await api(mgr, "POST", "/api/v1/customer-display/terminals", {
    installation_id: crypto.randomUUID(),
    name: `手帳煙霧 ${RUN}-${Math.random().toString(36).slice(2, 6)}`,
  });
  await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminal.json.id}/pair`, { pairing_code: code });
  await page.waitForSelector(".kiosk-standby-title", { timeout: 10000 });
  await page.waitForSelector(".camping-scene.is-ready", { timeout: 10000 });
  return { page, terminalId: terminal.json.id };
}

const sceneMode = (page) => page.getAttribute(".camping-scene", "data-mode");

const pageErrors = [];
const browser = await chromium.launch();
let mgr = null;
const carts = [];
try {
  mgr = (await api(null, "POST", "/api/v1/auth/login", { username: "dev-manager", password: "dev-test-123456" })).json.access_token;
  const current = await api(mgr, "GET", "/api/v1/cash-sessions/current");
  if (current.json === null) await api(mgr, "POST", "/api/v1/cash-sessions/open", { opening_float: "2000" });
  const seller = await api(mgr, "POST", "/api/v1/contacts", {
    name: `手帳煙霧賣方 ${RUN}`,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER"],
  });
  const acq = await api(
    mgr,
    "POST",
    "/api/v1/acquisitions",
    {
      type: "BUYOUT",
      contact_id: seller.json.id,
      payout_method: "CASH",
      items: [
        { name: `手沖壺 ${RUN}`, grade: "A", listed_price: "1200", acquisition_cost: "400" },
        { name: `露營燈 ${RUN}`, grade: "A", listed_price: "680", acquisition_cost: "200" },
        { name: `折疊椅 ${RUN}`, grade: "B", listed_price: "800", acquisition_cost: "250" },
        { name: `鋁杯 ${RUN}`, grade: "A", listed_price: "250", acquisition_cost: "60" },
      ],
    },
    { "Idempotency-Key": `ledger-${RUN}` },
  );
  const [kettle, lamp, chair, cup] = acq.json.item_codes.map((code) => ({ line_type: "SERIALIZED", item_code: code }));

  const context = await browser.newContext({ viewport: { width: 810, height: 1080 } });
  const { page, terminalId } = await pair(context, mgr);
  let revision = null;
  const put = async (lines, extra = {}) => {
    const res = await api(mgr, "PUT", `/api/v1/customer-display/terminals/${terminalId}/cart`, { expected_revision: revision, lines, ...extra });
    if (res.status >= 300) throw new Error(`PUT cart ${res.status} ${JSON.stringify(res.json)}`);
    revision = res.json.revision;
    carts.push({ terminalId, revision: () => revision, id: res.json.id });
    return res.json;
  };

  // ── 第一件 ──
  await put([kettle]);
  await page.waitForSelector(`[data-ledger-key]`, { timeout: 15000 });
  ok("開始結帳：動畫帶到營桌、第一人稱手帳", (await sceneMode(page)) === "cart");
  await page.waitForTimeout(1250);
  await page.screenshot({ path: join(SHOTS, "01-first-item-writing.png") });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: join(SHOTS, "02-first-item.png") });
  const firstRowVisible = await page.$eval("[data-ledger-key]", (el) => getComputedStyle(el).clipPath === "none" || getComputedStyle(el).clipPath === "");
  ok("寫完之後整行清楚可讀（沒有殘留遮罩）", firstRowVisible);

  // ── 連續快速掃描 ──
  await put([kettle, lamp]);
  await page.waitForTimeout(150);
  await put([kettle, lamp, chair]);
  await page.waitForTimeout(250);
  await page.screenshot({ path: join(SHOTS, "03-rapid-writing.png") });
  await page.waitForTimeout(1200);
  const rows = await page.$$eval("[data-ledger-key]", (els) => els.map((el) => ({ text: el.textContent, clip: getComputedStyle(el).clipPath })));
  ok("連續掃描三件都顯示、沒有卡在遮罩", rows.length === 3 && rows.every((r) => r.clip === "none" || r.clip === ""), JSON.stringify(rows.map((r) => r.clip)));
  await page.screenshot({ path: join(SHOTS, "04-three-items.png") });

  // ── 刪除 ──
  await put([kettle, chair]);
  await page.waitForSelector("[data-ledger-ghost]", { timeout: 5000 });
  await page.waitForTimeout(200);
  await page.screenshot({ path: join(SHOTS, "05-strike.png") });
  await page.waitForTimeout(900);
  ok("刪除後剩兩行", (await page.$$("[data-ledger-key]")).length === 2 && (await page.$$("[data-ledger-ghost]")).length === 0);

  // ── 同品項數量 1→2（一般商品）──
  const product = await api(mgr, "POST", "/api/v1/catalog-products", { sku: `LEDGER-GAS-${RUN}`, name: `瓦斯罐 ${RUN}`, unit_price: "120" });
  const supplier = await api(mgr, "POST", "/api/v1/suppliers", { name: `手帳煙霧供應商 ${RUN}` });
  const po = await api(mgr, "POST", "/api/v1/purchase-orders", {
    supplier_id: supplier.json.id,
    lines: [{ catalog_product_id: product.json.id, qty: 5, unit_cost: "60" }],
    submit: true,
  });
  await api(mgr, "POST", `/api/v1/purchase-orders/${po.json.id}/receive`, { lines: [{ line_id: po.json.lines[0].id, qty: 5 }] }, { "Idempotency-Key": `ledger-recv-${RUN}` });
  const gas = (qty) => ({ line_type: "CATALOG", catalog_product_id: product.json.id, qty });
  await put([kettle, chair, gas(1)]);
  await page.waitForTimeout(1200);
  const before = (await page.$$("[data-ledger-key]")).length;
  await put([kettle, chair, gas(2)]);
  await page.waitForTimeout(250);
  await page.screenshot({ path: join(SHOTS, "05b-qty-amend.png") });
  await page.waitForTimeout(1000);
  const gasRow = await page.$eval(`[data-ledger-key^="CATALOG"]`, (el) => ({ qty: el.querySelector(".kiosk-cart-qty").textContent, clip: getComputedStyle(el.querySelector(".kiosk-cart-qty")).clipPath }));
  ok("數量 1→2：不新增一行、只改數量", (await page.$$("[data-ledger-key]")).length === before && gasRow.qty.includes("2") && (gasRow.clip === "none" || gasRow.clip === ""), JSON.stringify(gasRow));

  // ── 付款 ──
  const lines = [kettle, chair, cup];
  await put(lines);
  await page.waitForTimeout(900);
  const quote = await api(mgr, "POST", "/api/v1/sales/quote", { lines });
  const total = String(quote.json.total);
  await put(lines, { tenders: [{ tender_type: "CASH", amount: total }] });
  const begun = await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminalId}/cart/begin-checkout`, { expected_revision: revision });
  ok("開始付款", begun.status === 200, `status=${begun.status}`);
  revision = begun.json?.revision ?? revision;
  await page.waitForTimeout(1500);
  const phase = await page.getAttribute(".kiosk-cart-shell", "data-phase");
  ok("付款中：筆放到紙旁", phase === "processing" || phase === "paying", phase);
  await page.screenshot({ path: join(SHOTS, "06-paying.png") });
  const sale = await api(
    mgr,
    "POST",
    "/api/v1/sales",
    { lines, tenders: [{ tender_type: "CASH", amount: total }], cart_session_id: begun.json?.id, cart_revision: revision },
    { "Idempotency-Key": `ledger-sale-${RUN}` },
  );
  ok("成交", sale.status === 201, `status=${sale.status} ${sale.status !== 201 ? JSON.stringify(sale.json) : ""}`);
  await page.waitForSelector('h1:has-text("交易已完成")', { timeout: 15000 });
  ok("成交：留在手帳、背景舉杯（paid）", (await sceneMode(page)) === "paid");
  await page.waitForTimeout(700);
  await page.screenshot({ path: join(SHOTS, "07-check-drawing.png") });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: join(SHOTS, "08-paid.png") });
  const dash = await page.$eval(".ledger-check-path", (el) => Number(getComputedStyle(el).strokeDashoffset.replace("px", "")));
  ok("勾勾畫完", Math.abs(dash) < 1, `dashoffset=${dash}`);
  const stamp = await page.$eval(".ledger-stamp", (el) => Number(getComputedStyle(el).opacity));
  ok("印章蓋上", stamp > 0.9, `opacity=${stamp}`);
  await context.close();

  // ── 減少動態效果 ──
  const calm = await browser.newContext({ viewport: { width: 810, height: 1080 }, reducedMotion: "reduce" });
  const calmPaired = await pair(calm, mgr);
  const calmCart = await api(mgr, "PUT", `/api/v1/customer-display/terminals/${calmPaired.terminalId}/cart`, { expected_revision: null, lines: [lamp] });
  carts.push({ terminalId: calmPaired.terminalId, revision: () => calmCart.json.revision, id: calmCart.json.id });
  await calmPaired.page.waitForSelector("[data-ledger-key]", { timeout: 15000 });
  await calmPaired.page.waitForTimeout(150);
  const calmClip = await calmPaired.page.$eval("[data-ledger-key]", (el) => getComputedStyle(el).clipPath);
  ok("減少動態效果：商品直接顯示", calmClip === "none" || calmClip === "", calmClip);
  await calmPaired.page.screenshot({ path: join(SHOTS, "09-reduced-motion.png") });
  await calm.close();

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  ok("流程例外", false, String(error));
  const p = browser.contexts().flatMap((c) => c.pages())[0];
  if (p) await p.screenshot({ path: join(SHOTS, "99-failure.png") });
} finally {
  await browser.close();
  for (const c of carts.slice(-2)) {
    await api(mgr, "POST", `/api/v1/customer-display/terminals/${c.terminalId}/cart/cancel`, { expected_revision: c.revision(), reason: "煙霧測試結束" }).catch(() => {});
  }
}
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
if (failures.length > 0) process.exitCode = 1;
