// 線上 LINE Pay 煙霧（docs/44 §4.4.2；O5）：真 backend／Postgres ＋ 本機 Worker/D1 ＋ 真客人頁、POS ＋ **假 LINE Pay**。
// 假 LINE Pay（本檔起在 :8955）模擬 request／付款頁／confirm／check／refund；真沙盒驗收由店主手機掃碼另做。
// 客人選 LINE Pay、填手機條碼 → 跳到付款頁按付款 → 導回訂單頁「已付款」→ 開著的 POS 自動成立銷售並提示
// → 作廢銷售走交易號退款；另一張在付款頁按取消 → 訂單頁「已取消、沒有扣款」→ POS 標等待付款、不能帶入收現金。
// 需：wrangler dev 帶 --var LINEPAY_CHANNEL_ID／LINEPAY_CHANNEL_SECRET／LINEPAY_API_BASE=http://127.0.0.1:8955，
//     backend 的 LINEPAY_API_BASE 也指向 :8955（作廢退款用）。只准對隔離測試環境執行（SMOKE_ALLOW_WRITE=1）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium, devices } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const BASE = process.env.SMOKE_BASE ?? "http://localhost:3500";
const API = process.env.SMOKE_API ?? "http://localhost:8114";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8799";
const FAKE_PORT = Number(process.env.SMOKE_LINEPAY_PORT ?? 8955);
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/online-linepay");
mkdirSync(SHOTS, { recursive: true });

// ── 假 LINE Pay ──
const txs = new Map();
const refunds = [];
let next = 2026100899000000000n;
const fake = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${FAKE_PORT}`);
  const body = await new Promise((resolve) => {
    let data = ""; req.on("data", (c) => { data += c; }); req.on("end", () => resolve(data));
  });
  const reply = (text) => { res.writeHead(200, { "Content-Type": "application/json" }); res.end(text); };
  if (req.method === "POST" && url.pathname === "/v4/payments/request") {
    const r = JSON.parse(body);
    const id = String(++next);
    txs.set(id, { amount: r.amount, state: "reserved", confirmUrl: r.redirectUrls.confirmUrl, cancelUrl: r.redirectUrls.cancelUrl });
    return reply(`{"returnCode":"0000","returnMessage":"Success.","info":{"transactionId":${id},` +
      `"paymentUrl":{"web":"http://127.0.0.1:${FAKE_PORT}/pay/${id}","app":"line://pay/${id}"}}}`);
  }
  const page = /^\/pay\/(\d+)$/.exec(url.pathname);
  if (page) {
    const tx = txs.get(page[1]);
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    return res.end(`<!doctype html><meta name="viewport" content="width=device-width"><h1>假 LINE Pay</h1>` +
      `<p>NT$${tx.amount}</p><a id="pay" href="/pay/${page[1]}/ok">付款</a> <a id="cancel" href="${tx.cancelUrl}">取消</a>`);
  }
  const ok = /^\/pay\/(\d+)\/ok$/.exec(url.pathname);
  if (ok) {
    const tx = txs.get(ok[1]); tx.state = "authorized";
    res.writeHead(302, { Location: `${tx.confirmUrl}&transactionId=${ok[1]}` }); return res.end();
  }
  const confirm = /^\/v4\/payments\/(\d+)\/confirm$/.exec(url.pathname);
  if (confirm) {
    const tx = txs.get(confirm[1]);
    if (!tx || tx.state !== "authorized" || JSON.parse(body).amount !== tx.amount) return reply('{"returnCode":"1150","returnMessage":"no"}');
    tx.state = "captured"; return reply(`{"returnCode":"0000","returnMessage":"Success.","info":{"transactionId":${confirm[1]}}}`);
  }
  const check = /^\/v4\/payments\/requests\/(\d+)\/check$/.exec(url.pathname);
  if (check) {
    const tx = txs.get(check[1]);
    return reply(`{"returnCode":"${tx ? { reserved: "0000", authorized: "0110", captured: "0123" }[tx.state] : "1150"}","returnMessage":"ok"}`);
  }
  const refund = /^\/v4\/payments\/(\d+)\/refund$/.exec(url.pathname);
  if (refund) { refunds.push(url.pathname); return reply('{"returnCode":"0000","returnMessage":"Success.","info":{"refundTransactionId":1}}'); }
  res.writeHead(404); res.end();
});
await new Promise((resolve) => fake.listen(FAKE_PORT, "127.0.0.1", resolve));

let token = "";
async function api(method, path, body) {
  const response = await fetch(`${API}/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}
async function must(method, path, body) {
  const r = await api(method, path, body);
  assert.ok(r.status < 300, `${method} ${path}: ${r.status} ${JSON.stringify(r.body)}`);
  return r.body;
}
async function waitFor(fn, label, ms = 30000) {
  const deadline = Date.now() + ms;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`等不到：${label}`);
    await new Promise((r) => setTimeout(r, 500));
  }
}
const results = [];
const ok = (name, pass, detail = "") => { results.push(pass); console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`); };

token = (await must("POST", "/auth/login", { username: USERNAME, password: PASSWORD })).access_token;
const run = randomUUID().slice(0, 6);
await must("POST", "/menu-items", { name: `拿鐵-${run}`, unit_price: "150", category: `咖啡-${run}` });
const settings = await must("GET", "/settings");
await must("PATCH", "/settings", { dine_in_tables: [...new Set([...(settings.dine_in_tables ?? []), "A1"])] });
await must("POST", "/online-order/publish");
const code = (await must("GET", "/online-order/status")).tables.find((t) => t.label === "A1")?.code;
await must("PUT", "/online-orders/accepting", { accepting: true });

const browser = await chromium.launch();
const errors = [];
const desk = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
desk.on("pageerror", (e) => errors.push(`POS：${e}`));
const guestCtx = await browser.newContext({ ...devices["iPhone 13"] });
const guest = await guestCtx.newPage();
guest.on("pageerror", (e) => errors.push(`客人頁：${e}`));

async function orderLatte(note) {
  await guest.goto(`${ORDER}/t/${code}`, { waitUntil: "networkidle" });
  await guest.getByRole("button", { name: `咖啡-${run}`, exact: true }).click();
  await guest.locator(".item", { hasText: `拿鐵-${run}` }).getByRole("button", { name: /加入/ }).click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  await guest.locator("#order-note").fill(note);
  await guest.getByLabel(/LINE Pay/).check();
}

try {
  // POS 先開著（付好的單要由它自動成立）
  await skipOpeningCheckRedirect(desk, BASE);
  await desk.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await desk.fill('input[name="username"]', USERNAME);
  await desk.fill('input[name="password"]', PASSWORD);
  await desk.click('button:has-text("登入")');
  await desk.waitForURL(`${BASE}/`);
  await desk.goto(`${BASE}/pos`, { waitUntil: "networkidle" });

  // ① 付款成功
  const note = `LINE付-${run}`;
  await orderLatte(note);
  await guest.locator("#invoice-kind").selectOption("carrier");
  await guest.getByLabel("手機條碼", { exact: true }).fill("/ABC+123");
  await guest.screenshot({ path: join(SHOTS, "01-guest-choose-linepay.png"), fullPage: true });
  await guest.getByRole("button", { name: "用 LINE Pay 付款" }).click();
  await guest.waitForURL(/127\.0\.0\.1:8955\/pay\/\d+$/, { timeout: 45000 });
  ok("跳到 LINE Pay 付款頁、金額正確", (await guest.locator("body").innerText()).includes("NT$150"));
  await guest.locator("#pay").click();
  await guest.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 30000 });
  await guest.getByText("LINE Pay 已付款，開始製作囉。").waitFor({ timeout: 30000 });
  ok("導回訂單頁：已付款，網址上的參數拿掉了", !guest.url().includes("linepay="));
  await guest.screenshot({ path: join(SHOTS, "02-guest-paid.png"), fullPage: true });

  const paid = await waitFor(async () => (await must("GET", "/online-orders")).orders.find((o) => o.note === note && o.sale_id), "POS 自動成立銷售", 45000);
  ok("開著的 POS 自動成立銷售", paid.sync_status === "SETTLED");
  const notice = desk.locator(".pos-online-paid", { hasText: "線上 LINE Pay 已付款：桌號 A1 $150" });
  await notice.waitFor({ timeout: 20000 });
  ok("POS 上方提示已付款的線上單", true, (await notice.innerText()).replace(/\n/g, " "));
  await desk.screenshot({ path: join(SHOTS, "03-pos-notice.png") });
  const sale = await must("GET", `/sales/${paid.sale_id}`);
  ok("銷售：LINE Pay $150、不再扣款", JSON.stringify(sale.tenders.map((t) => [t.tender_type, t.amount])) === '[["LINE_PAY","150"]]');
  const voided = await api("POST", `/sales/${paid.sale_id}/void`, {});
  ok("作廢成功", voided.status === 200, `${voided.status} ${JSON.stringify(voided.body)}`);
  ok("作廢走線上退款（交易號）", refunds.length === 1 && /^\/v4\/payments\/\d{19}\/refund$/.test(refunds[0]), JSON.stringify(refunds));

  // ② 取消付款
  const note2 = `LINE取消-${run}`;
  await orderLatte(note2);
  await guest.getByRole("button", { name: "用 LINE Pay 付款" }).click();
  await guest.waitForURL(/127\.0\.0\.1:8955\/pay\/\d+$/, { timeout: 45000 });
  await guest.locator("#cancel").click();
  await guest.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 30000 });
  await guest.getByText("LINE Pay 付款已取消，沒有扣款。").waitFor({ timeout: 20000 });
  ok("取消後客人頁：沒有扣款、可以重付", (await guest.getByRole("button", { name: "用 LINE Pay 付款" }).count()) === 1);
  await guest.screenshot({ path: join(SHOTS, "04-guest-cancelled.png"), fullPage: true });
  await desk.getByRole("button", { name: /線上訂單/ }).click();
  const row = desk.getByRole("dialog", { name: "線上訂單" }).getByRole("listitem").filter({ hasText: note2 });
  await row.getByText("等待 LINE Pay 付款").waitFor({ timeout: 20000 });
  ok("POS：等待 LINE Pay 付款、不能帶入收現金", (await row.getByRole("button", { name: "帶入結帳" }).count()) === 0);
  await desk.screenshot({ path: join(SHOTS, "05-pos-awaiting-linepay.png") });
  const overflow = await guest.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  ok("手機不橫向捲動", overflow <= 0, `溢出 ${overflow}px`);
  ok("頁面無 JS 例外", errors.length === 0, errors.join(" | "));
} catch (error) {
  ok("流程例外", false, String(error));
  await guest.screenshot({ path: join(SHOTS, "zz-guest.png"), fullPage: true }).catch(() => {});
  await desk.screenshot({ path: join(SHOTS, "zz-desk.png"), fullPage: true }).catch(() => {});
} finally {
  // 取消這次留下來沒付的單：留著會占住同一個 IP 的未付款上限，下一個煙霧就送不了單
  const left = (await api("GET", "/online-orders")).body?.orders?.filter((o) => o.note?.endsWith(run) && o.sync_status === "IMPORTED") ?? [];
  for (const o of left) await api("POST", `/online-orders/${o.id}/cancel`);
  await browser.close();
  fake.close();
}
const failed = results.filter((p) => !p).length;
console.log(`\n${results.length - failed}/${results.length} PASS；截圖 ${SHOTS}`);
process.exit(failed ? 1 : 0);
