// 顧客螢幕結帳手帳煙霧（店主 2026-09-27：第一人稱露營手帳）：
//   第一件：鏡頭拉近＋桌面滑上來，手寫一行（寫到一半截圖、寫完截圖）
//   連續快速掃描：兩筆幾乎同時進來，動畫不排隊、最後全部清楚顯示
//   同品項數量 1→2：不新增一行，只改數量
//   刪除：該行劃線後收起
//   開始付款 → 付款處理中 → 成交：勾勾一筆畫、印章、背景舉杯（paid）
//   減少動態效果：商品直接顯示，沒有手寫遮罩
// 需 backend + frontend 已起、已 seed（dev-manager、dev-kiosk）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/kiosk-ledger-smoke.mjs
import { copyFileSync, mkdirSync, rmSync } from "node:fs";
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
  // 連拍第一件寫字的過程，挑一張「字寫到一半」的
  let writingShot = false;
  for (let i = 0; i < 16 && !writingShot; i += 1) {
    const clip = await page.$eval("[data-ledger-key]", (el) => el.style.clipPath);
    if (clip && clip !== "none" && !clip.includes("100%")) {
      await page.screenshot({ path: join(SHOTS, "qa02-scanning-first-item.png") });
      writingShot = true;
    } else await page.waitForTimeout(60);
  }
  ok("第一件：看得到手正在寫、字寫到一半", writingShot);
  await page.waitForTimeout(1600);
  const firstRowVisible = await page.$eval("[data-ledger-key]", (el) => getComputedStyle(el).clipPath === "none" || getComputedStyle(el).clipPath === "");
  ok("寫完之後整行清楚可讀（沒有殘留遮罩）", firstRowVisible);

  // ── 連續快速掃描：1 秒內再加三件（店主 2026-10-01：要順）──
  // 每一幀記下手的位置：打斷上一筆時手不能瞬移（一幀移動超過 90px 就算跳）
  await page.evaluate(() => {
    window.__hand = [];
    const t0 = performance.now();
    const f = () => {
      const hand = document.querySelector(".ledger-hand");
      if (hand) {
        const m = new DOMMatrix(getComputedStyle(hand).transform);
        window.__hand.push([performance.now() - t0, m.m41, m.m42]);
      }
      if (performance.now() - t0 < 2500) requestAnimationFrame(f);
    };
    requestAnimationFrame(f);
  });
  await put([kettle, lamp]);
  await page.waitForTimeout(250);
  await put([kettle, lamp, chair]);
  await page.waitForTimeout(250);
  await put([kettle, lamp, chair, cup]);
  await page.waitForTimeout(150);
  await page.screenshot({ path: join(SHOTS, "03-rapid-writing.png") });
  await page.waitForTimeout(2300);
  const trace = await page.evaluate(() => window.__hand);
  let maxJump = 0;
  for (let i = 1; i < trace.length; i += 1) {
    const [t0, x0, y0] = trace[i - 1];
    const [t1, x1, y1] = trace[i];
    const perFrame = Math.hypot(x1 - x0, y1 - y0) / Math.max(1, (t1 - t0) / 16.7);
    maxJump = Math.max(maxJump, perFrame);
  }
  ok("1 秒內連加三件：手一路移過去、沒有瞬移", trace.length > 30 && maxJump < 90, `最大每幀位移 ${maxJump.toFixed(0)}px，${trace.length} 幀`);
  const rows = await page.$$eval("[data-ledger-key]", (els) => els.map((el) => ({ text: el.textContent, clip: getComputedStyle(el).clipPath })));
  ok("連加的四件都寫完、沒有卡在遮罩", rows.length === 4 && rows.every((r) => r.clip === "none" || r.clip === ""), JSON.stringify(rows.map((r) => r.clip)));
  await page.screenshot({ path: join(SHOTS, "qa03-four-items.png") });

  // ── 刪除：筆移過去、劃兩筆、變淡停一下、收起來，總額最後才改 ──
  const totalBefore = await page.textContent(".kiosk-cart-grand-total strong");
  await put([kettle, chair]);
  await page.waitForSelector("[data-ledger-ghost]", { timeout: 5000 });
  // 連拍刪除過程（截圖本身有延遲，固定時間點截不準）：每張記下筆的位置與劃線進度，挑四個階段
  const burst = [];
  let totalDuring = null;
  let ghostOpacity = -1;
  for (let i = 0; i < 14; i += 1) {
    const state = await page.evaluate(() => {
      const s1 = document.querySelector(".ledger-strike-1");
      const s2 = document.querySelector(".ledger-strike-2");
      const content = document.querySelector("[data-ledger-ghost] > div");
      const len = (el) => Number(el?.getAttribute("stroke-dasharray") ?? 0);
      const off = (el) => Number(el?.getAttribute("stroke-dashoffset") ?? 0);
      return {
        ghost: Boolean(document.querySelector("[data-ledger-ghost]")),
        p1: s1 && len(s1) ? 1 - off(s1) / len(s1) : 0,
        p2: s2 && len(s2) ? 1 - off(s2) / len(s2) : 0,
        fade: content ? Number(getComputedStyle(content).opacity) : 1,
        total: document.querySelector(".kiosk-cart-grand-total strong")?.textContent,
      };
    });
    if (!state.ghost) break;
    const file = `burst-${String(i).padStart(2, "0")}.png`;
    await page.screenshot({ path: join(SHOTS, file) });
    burst.push({ ...state, file });
    if (totalDuring === null) totalDuring = state.total;
    if (state.fade < 0.7) ghostOpacity = state.fade;
  }
  const pick = (name, found) => {
    if (found) copyFileSync(join(SHOTS, found.file), join(SHOTS, name));
    ok(`刪除截圖：${name}`, Boolean(found));
  };
  pick("qa05-delete-pen-touching.png", burst[0]);
  pick("qa06-delete-first-strike.png", burst.slice(1).find((b) => b.p1 > 0.05 && b.p2 < 0.99) ?? burst[1]);
  pick("qa07-delete-struck-through.png", burst.find((b) => b.fade < 0.7));
  pick("qa08-delete-before-collapse.png", [...burst].reverse().find((b) => b.fade < 0.7));
  for (const b of burst) rmSync(join(SHOTS, b.file), { force: true });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: join(SHOTS, "qa09-after-delete.png") });
  const totalAfter = await page.textContent(".kiosk-cart-grand-total strong");
  ok("刪除：劃掉時那行變淡但還在", ghostOpacity > 0.3 && ghostOpacity < 0.7, `opacity=${ghostOpacity}`);
  ok("刪除：總額等那行收起來才更新", totalDuring === totalBefore && totalAfter !== totalBefore, `${totalBefore} → ${totalDuring} → ${totalAfter}`);
  ok("刪除後剩兩行、沒有殘留", (await page.$$("[data-ledger-key]")).length === 2 && (await page.$$("[data-ledger-ghost]")).length === 0);
  ok("不跳「已移除」通知", (await page.locator(".kiosk-cart-change.is-removed").count()) === 0);

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
  await page.screenshot({ path: join(SHOTS, "qa04-updating-quantity.png") });
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
  ok("付款中：筆放到紙上、總額旁小點點", phase === "paying" && (await page.locator(".ledger-breath").count()) === 1, phase);
  await page.screenshot({ path: join(SHOTS, "qa10-payment-pending.png") });
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
  await page.waitForTimeout(650);
  await page.screenshot({ path: join(SHOTS, "qa11a-check-drawing.png") });
  await page.waitForTimeout(1900);
  await page.screenshot({ path: join(SHOTS, "qa11-payment-success.png") });
  const dash = await page.$eval(".ledger-check-path", (el) => Number(getComputedStyle(el).strokeDashoffset.replace("px", "")));
  ok("勾勾畫完", Math.abs(dash) < 1, `dashoffset=${dash}`);
  const stamp = await page.$eval(".ledger-stamp", (el) => Number(getComputedStyle(el).opacity));
  ok("印章蓋上", stamp > 0.9, `opacity=${stamp}`);
  // ── 付款失敗：金額對不上 → POS 退回可修改 → 手帳寫「付款未完成」，不蓋章 ──
  await page.waitForSelector(".kiosk-standby-title", { timeout: 20000 });
  revision = null;
  const failLines = [lamp];
  await put(failLines);
  await page.waitForSelector("[data-ledger-key]", { timeout: 15000 });
  await page.waitForTimeout(1500);
  const failQuote = await api(mgr, "POST", "/api/v1/sales/quote", { lines: failLines });
  await put(failLines, { tenders: [{ tender_type: "CASH", amount: String(failQuote.json.total) }] });
  const failBegun = await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminalId}/cart/begin-checkout`, { expected_revision: revision });
  revision = failBegun.json?.revision ?? revision;
  await page.waitForTimeout(800);
  const failed = await api(
    mgr,
    "POST",
    "/api/v1/sales",
    { lines: failLines, tenders: [{ tender_type: "CASH", amount: String(Number(failQuote.json.total) + 1) }], cart_session_id: failBegun.json?.id, cart_revision: revision },
    { "Idempotency-Key": `ledger-fail-${RUN}` },
  );
  await page.waitForSelector(".ledger-note.is-warn", { timeout: 15000 });
  ok("付款失敗：寫「付款未完成」、不蓋章", (await page.locator(".ledger-stamp").count()) === 0, `sale status=${failed.status}`);
  await page.screenshot({ path: join(SHOTS, "qa12-payment-failed.png") });
  const current2 = await api(mgr, "GET", `/api/v1/customer-display/terminals/${terminalId}/cart/current`);
  revision = current2.json?.revision ?? revision;
  await context.close();

  // ── 減少動態效果 ──
  const calm = await browser.newContext({ viewport: { width: 810, height: 1080 }, reducedMotion: "reduce" });
  const calmPaired = await pair(calm, mgr);
  await calmPaired.page.waitForTimeout(800);
  await calmPaired.page.screenshot({ path: join(SHOTS, "qa01-idle-hero.png") });
  const calmCart = await api(mgr, "PUT", `/api/v1/customer-display/terminals/${calmPaired.terminalId}/cart`, { expected_revision: null, lines: [lamp] });
  carts.push({ terminalId: calmPaired.terminalId, revision: () => calmCart.json.revision, id: calmCart.json.id });
  await calmPaired.page.waitForSelector("[data-ledger-key]", { timeout: 15000 });
  await calmPaired.page.waitForTimeout(150);
  const calmClip = await calmPaired.page.$eval("[data-ledger-key]", (el) => getComputedStyle(el).clipPath);
  ok("減少動態效果：商品直接顯示", calmClip === "none" || calmClip === "", calmClip);
  await calmPaired.page.screenshot({ path: join(SHOTS, "qa13-reduced-motion.png") });
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
