// LINE Pay 綁定載具（店主 2026-10-17）真瀏覽器煙霧：真客人頁 ＋ 本機 Worker/D1 ＋ 假 LINE Pay。
// 三種情況各付一次：①沒填、LINE Pay 有綁 → 存綁定的載具 ②沒填、沒綁 → 印紙本 ③自己填了 → 以填的為準。
// 每次付好看訂單頁的發票說明，並用 POS 拉單確認開發票用的載具。只連隔離的 Worker（不碰 POS／Postgres），需：
//   wrangler dev 帶 --var LINEPAY_CHANNEL_ID／LINEPAY_CHANNEL_SECRET／
//   LINEPAY_API_BASE=http://127.0.0.1:<SMOKE_LINEPAY_PORT>，TURNSTILE 用 Cloudflare 測試金鑰；
//   SMOKE_INTEGRATION_SECRET＝該 Worker 的 INTEGRATION_SECRET（模擬 POS 發佈菜單與拉單）。
import assert from "node:assert/strict";
import { createHash, createHmac, randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { chromium } from "playwright";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8797";
assert.match(ORDER, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/, "只對本機 Worker 跑");
const FAKE_PORT = Number(process.env.SMOKE_LINEPAY_PORT ?? 8957);
const SECRET = process.env.SMOKE_INTEGRATION_SECRET;
assert.ok(SECRET, "需要 SMOKE_INTEGRATION_SECRET");
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-linepay-carrier-shots";
mkdirSync(SHOTS, { recursive: true });

// ── 假 LINE Pay：開交易、付款頁（按「付款」導回 confirmUrl）、請款（依 bound 帶回綁定的載具） ──
const txs = new Map();
let next = 2026101799000000000n;
let bound = null;
const fake = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${FAKE_PORT}`);
  const body = await new Promise((resolve) => {
    let data = ""; req.on("data", (c) => { data += c; }); req.on("end", () => resolve(data));
  });
  const reply = (text) => { res.writeHead(200, { "Content-Type": "application/json" }); res.end(text); };
  if (req.method === "POST" && url.pathname === "/v4/payments/request") {
    const r = JSON.parse(body);
    const id = String(++next);
    txs.set(id, { amount: r.amount, confirmUrl: r.redirectUrls.confirmUrl, bound });
    return reply(`{"returnCode":"0000","returnMessage":"Success.","info":{"transactionId":${id},` +
      `"paymentUrl":{"web":"http://127.0.0.1:${FAKE_PORT}/pay/${id}","app":"line://pay/${id}"}}}`);
  }
  const page = /^\/pay\/(\d+)$/.exec(url.pathname);
  if (page) {
    const tx = txs.get(page[1]);
    tx.authorized = true;
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    return res.end(`<!doctype html><meta name="viewport" content="width=device-width"><h1>假 LINE Pay</h1>` +
      `<p>NT$${tx.amount}</p><a id="pay" href="${tx.confirmUrl}">付款</a>`);
  }
  const confirm = /^\/v4\/payments\/(\d+)\/confirm$/.exec(url.pathname);
  if (confirm) {
    const tx = txs.get(confirm[1]);
    if (!tx?.authorized) return reply('{"returnCode":"1150","returnMessage":"not authorized"}');
    tx.captured = true;
    const reference = tx.bound === null ? ""
      : `,"merchantReference":{"affiliateCards":[{"cardType":"MOBILE_CARRIER","cardId":"${tx.bound}"}]}`;
    return reply(`{"returnCode":"0000","returnMessage":"Success.","info":{"transactionId":${confirm[1]}${reference}}}`);
  }
  const check = /^\/v4\/payments\/requests\/(\d+)\/check$/.exec(url.pathname);
  if (check) {
    const tx = txs.get(check[1]);
    return reply(`{"returnCode":"${tx?.captured ? "0123" : tx?.authorized ? "0110" : "0000"}","returnMessage":"ok"}`);
  }
  res.writeHead(404); res.end();
});
await new Promise((resolve) => fake.listen(FAKE_PORT, "127.0.0.1", resolve));

// ── 模擬 POS：簽章呼叫 /integration/*（規則同 online-order/src/auth.ts） ──
async function integration(method, path, body = "") {
  const timestamp = String(Math.floor(Date.now() / 1000)), nonce = randomUUID();
  const digest = createHash("sha256").update(body).digest("hex");
  const signature = createHmac("sha256", SECRET).update([method, path, timestamp, nonce, digest].join("\n")).digest("hex");
  const r = await fetch(ORDER + path, {
    method, body: method === "GET" ? undefined : body,
    headers: { "X-LuCamp-Timestamp": timestamp, "X-LuCamp-Nonce": nonce, "X-LuCamp-Signature": signature },
  });
  assert.ok(r.ok, `${method} ${path}: ${r.status} ${await r.clone().text()}`);
  return r.json();
}
await integration("PUT", "/integration/menu", JSON.stringify({
  version: Date.now(), published_at: new Date().toISOString(), store_name: "露坑", font: null,
  categories: [{ id: 1, name: "飲品" }],
  items: [{ id: 5, name: "拿鐵", description: "", category_id: 1, unit_price: 150, photo: null, available: true, remaining: null, option_groups: [] }],
}));
await integration("PUT", "/integration/store-status", JSON.stringify({ accepting: true }));
await integration("GET", "/integration/orders");

const browser = await chromium.launch();
try {
  const scenarios = [
    { name: "bound", bound: "ABC+123", typed: null,
      note: "發票會存入你 LINE Pay 綁定的手機條碼載具 /ABC+123。", pos: { carrier: "/ABC+123", tax_id: null } },
    { name: "paper", bound: null, typed: null,
      note: "你的 LINE Pay 沒有綁定載具，紙本發票請到櫃檯領取。", pos: { carrier: null, tax_id: null } },
    { name: "typed", bound: "/ABC+123", typed: "/XYZ.999",
      note: "發票會存入你輸入的手機條碼載具 /XYZ.999。", pos: { carrier: "/XYZ.999", tax_id: null } },
  ];
  for (const s of scenarios) {
    bound = s.bound;
    // 每次用新的瀏覽器分頁（不同 IP 名額照算，3 筆遠低於上限）。
    const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await guest.route(/challenges\.cloudflare\.com\/turnstile\/v0\/api\.js/, (route) => route.fulfill({
      contentType: "text/javascript",
      body: `(() => { let o = null;
        window.turnstile = { render(el, opts) { o = opts; setTimeout(() => o.callback("XXXX.DUMMY.TOKEN.XXXX"), 300); return "w1"; },
          reset() { setTimeout(() => o.callback("XXXX.DUMMY.TOKEN.XXXX"), 300); } };
        window.onTurnstileReady?.(); })();`,
    }));
    await guest.goto(ORDER);
    await guest.locator(".item").filter({ hasText: "拿鐵" }).first().locator(".item-add").click();
    await guest.getByRole("button", { name: /購物車 1 份/ }).click();
    await guest.getByLabel(/LINE Pay/).check();
    assert.equal(await guest.locator("#invoice-kind option:checked").textContent(), "LINE Pay 上已綁定");
    // 沒填的說明：有綁就存、不用輸入；沒綁才印紙本。
    await guest.getByText("發票會自動存進你在 LINE Pay 綁定的手機條碼載具，不需輸入；沒綁定就印紙本發票，請到櫃檯領取。").waitFor();
    if (s.name === "bound") await guest.screenshot({ path: `${SHOTS}/01-checkout-hint.png` });
    if (s.typed) {
      await guest.locator("#invoice-kind").selectOption("carrier");
      assert.equal(await guest.locator("#invoice-hint").textContent(), "", "手機條碼載具不另外提醒");
      await guest.locator("#invoice-code").fill(s.typed);
    }
    await guest.getByRole("button", { name: "用 LINE Pay 付款" }).click();
    await guest.waitForURL(new RegExp(`127\\.0\\.0\\.1:${FAKE_PORT}/pay/\\d+$`), { timeout: 90000 });
    await guest.locator("#pay").click();
    await guest.getByText("LINE Pay 已付款，開始製作囉。").waitFor({ timeout: 30000 });
    await guest.getByText(s.note).waitFor();
    await guest.screenshot({ path: `${SHOTS}/02-paid-${s.name}.png` });
    const pulled = await integration("GET", "/integration/orders");
    const order = pulled.orders.find((o) => o.payment_status === "PAID");
    assert.deepEqual(order.invoice, s.pos, `${s.name}: POS 開發票用的載具`);
    await integration("POST", `/integration/orders/${order.id}/status`, JSON.stringify({ sync_status: "IMPORTED" }));
    await guest.close();
    console.log(`PASS ${s.name}: ${s.note}`);
  }
} finally {
  await browser.close();
  fake.close();
}
