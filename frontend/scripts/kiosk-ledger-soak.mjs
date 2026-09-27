// 顧客螢幕結帳手帳長時間測試（店主 2026-09-27 定稿規格 AC）：跑 N 輪（預設 300）
//   新增 A → 新增 B → A 數量改 3 → 刪除 B → 奇數輪付款成交／偶數輪整筆取消
// 每 25 輪強制回收記憶體後記錄 DOM 節點數、事件監聽器數、JS heap，最後比對頭尾是否穩定。
// 需 backend + frontend 已起、已 seed（dev-manager、dev-kiosk）。
// 執行：SOAK_CYCLES=300 SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/kiosk-ledger-soak.mjs
import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const CYCLES = Number(process.env.SOAK_CYCLES ?? 300);
const RUN = Date.now().toString(36);
const STEP_MS = Number(process.env.SOAK_STEP_MS ?? 260);

async function api(token, method, path, body, extra = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...extra },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

async function stockedProduct(mgr, tag, price) {
  const product = await api(mgr, "POST", "/api/v1/catalog-products", { sku: `SOAK-${tag}-${RUN}`, name: `長測${tag} ${RUN}`, unit_price: String(price) });
  const supplier = await api(mgr, "POST", "/api/v1/suppliers", { name: `長測供應商${tag} ${RUN}` });
  const po = await api(mgr, "POST", "/api/v1/purchase-orders", {
    supplier_id: supplier.json.id,
    lines: [{ catalog_product_id: product.json.id, qty: 2000, unit_cost: String(Math.round(price / 2)) }],
    submit: true,
  });
  await api(mgr, "POST", `/api/v1/purchase-orders/${po.json.id}/receive`, { lines: [{ line_id: po.json.lines[0].id, qty: 2000 }] }, { "Idempotency-Key": `soak-recv-${tag}-${RUN}` });
  return product.json.id;
}

const mgr = (await api(null, "POST", "/api/v1/auth/login", { username: "dev-manager", password: "dev-test-123456" })).json.access_token;
const current = await api(mgr, "GET", "/api/v1/cash-sessions/current");
if (current.json === null) await api(mgr, "POST", "/api/v1/cash-sessions/open", { opening_float: "2000" });
const productA = await stockedProduct(mgr, "A", 150);
const productB = await stockedProduct(mgr, "B", 90);
const line = (id, qty) => ({ line_type: "CATALOG", catalog_product_id: id, qty });

const browser = await chromium.launch({ args: ["--js-flags=--expose-gc"] });
const page = await browser.newPage({ viewport: { width: 810, height: 1080 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
await page.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
await page.fill('input[name="username"]', "dev-kiosk");
await page.fill('input[name="password"]', "dev-test-123456");
await page.click('button:has-text("啟用裝置")');
await page.locator(".kiosk-pairing-code").waitFor({ timeout: 8000 });
const code = (await page.textContent(".kiosk-pairing-code"))?.trim();
const terminal = await api(mgr, "POST", "/api/v1/customer-display/terminals", { installation_id: crypto.randomUUID(), name: `長測 ${RUN}` });
const terminalId = terminal.json.id;
await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminalId}/pair`, { pairing_code: code });
await page.locator(".camping-scene.is-ready").waitFor({ timeout: 15000 });
const cdp = await page.context().newCDPSession(page);
await cdp.send("Performance.enable");

async function sample(label) {
  await cdp.send("HeapProfiler.collectGarbage");
  await page.waitForTimeout(300);
  const { metrics } = await cdp.send("Performance.getMetrics");
  const m = Object.fromEntries(metrics.map((x) => [x.name, x.value]));
  const liveDom = await page.evaluate(() => document.querySelectorAll("*").length);
  const row = { label, nodes: m.Nodes, liveDom, listeners: m.JSEventListeners, heapMB: +(m.JSHeapUsedSize / 1048576).toFixed(1) };
  console.log(JSON.stringify(row));
  return row;
}

let revision = null;
const put = async (lines, extra = {}) => {
  const res = await api(mgr, "PUT", `/api/v1/customer-display/terminals/${terminalId}/cart`, { expected_revision: revision, lines, ...extra });
  if (res.status >= 300) throw new Error(`PUT ${res.status} ${JSON.stringify(res.json)}`);
  revision = res.json.revision;
  return res.json;
};

const samples = [await sample("start (idle)")];
const t0 = Date.now();
for (let i = 1; i <= CYCLES; i += 1) {
  revision = null;
  await put([line(productA, 1)]);
  await page.waitForTimeout(STEP_MS);
  await put([line(productA, 1), line(productB, 1)]);
  await page.waitForTimeout(STEP_MS);
  await put([line(productA, 3), line(productB, 1)]);
  await page.waitForTimeout(STEP_MS);
  const cart = await put([line(productA, 3)]);
  await page.waitForTimeout(STEP_MS * 2);
  const mode = process.env.SOAK_MODE ?? "mixed";
  if (mode === "pay" || (mode === "mixed" && i % 2 === 1)) {
    const total = cart.snapshot.total;
    await put([line(productA, 3)], { tenders: [{ tender_type: "CASH", amount: total }] });
    const begun = await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminalId}/cart/begin-checkout`, { expected_revision: revision });
    if (begun.status !== 200) throw new Error(`begin ${begun.status} ${JSON.stringify(begun.json)}`);
    revision = begun.json.revision;
    const sale = await api(
      mgr,
      "POST",
      "/api/v1/sales",
      { lines: [line(productA, 3)], tenders: [{ tender_type: "CASH", amount: total }], cart_session_id: begun.json.id, cart_revision: revision },
      { "Idempotency-Key": `soak-sale-${RUN}-${i}` },
    );
    if (sale.status !== 201) throw new Error(`sale ${sale.status} ${JSON.stringify(sale.json)}`);
    // 用 locator 等：waitForSelector 會回傳元素參照，不釋放就會把被拆掉的畫面一直留在記憶體（假洩漏）
    await page.locator('h1:has-text("交易已完成")').waitFor({ timeout: 15000 });
    await page.waitForTimeout(1200);
    // 完成畫面約 10 秒後自動清除；長測不等那麼久，直接開下一筆（新購物車會蓋過完成畫面）
  } else {
    await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminalId}/cart/cancel`, { expected_revision: revision, reason: "長時間測試" });
    await page.locator(".kiosk-standby-title").waitFor({ timeout: 15000 });
    await page.waitForTimeout(400);
  }
  if (i % Number(process.env.SOAK_SAMPLE ?? 25) === 0) samples.push(await sample(`cycle ${i}`));
}
const minutes = ((Date.now() - t0) / 60000).toFixed(1);
// 最後回到待機再量一次（和開頭同一個畫面比）
await page.locator(".kiosk-standby-title").waitFor({ timeout: 20000 });
await page.waitForTimeout(1500);
const end = await sample("end (idle)");
const first = samples[1] ?? samples[0];
const last = samples[samples.length - 1];
console.log(`\n${CYCLES} 輪、${minutes} 分鐘`);
console.log(`待機 DOM 節點：開頭 ${samples[0].nodes} → 結尾 ${end.nodes}`);
console.log(`事件監聽器：開頭 ${samples[0].listeners} → 結尾 ${end.listeners}`);
console.log(`JS heap：第 25 輪 ${first.heapMB}MB → 第 ${CYCLES} 輪 ${last.heapMB}MB；待機開頭 ${samples[0].heapMB}MB → 結尾 ${end.heapMB}MB`);
console.log(`頁面例外：${errors.length} 筆 ${errors.slice(0, 3).join(" / ")}`);
await browser.close();
