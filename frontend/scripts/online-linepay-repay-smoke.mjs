// LINE Pay 重新付款要重新驗證人機（docs/44 §8.4）真瀏覽器煙霧：真客人頁 ＋ 本機 Worker/D1 ＋ 假 LINE Pay。
// 客人選 LINE Pay 送單 → 付款頁按取消 → 訂單頁按「用 LINE Pay 付款」→ 雲端要求驗證（403）→ 客人頁就地驗證
// → 重送成功、到第二筆付款頁。只連隔離的 Worker（不碰 POS／Postgres），需：
//   wrangler dev（獨立 --persist-to）帶 --var LINEPAY_CHANNEL_ID／LINEPAY_CHANNEL_SECRET／
//   LINEPAY_API_BASE=http://127.0.0.1:<SMOKE_LINEPAY_PORT>，TURNSTILE 用 Cloudflare 測試金鑰；
//   SMOKE_INTEGRATION_SECRET＝該 Worker 的 INTEGRATION_SECRET（模擬 POS 發佈菜單與心跳）。
import assert from "node:assert/strict";
import { createHash, createHmac, randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { chromium } from "playwright";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8797";
const FAKE_PORT = Number(process.env.SMOKE_LINEPAY_PORT ?? 8956);
const SECRET = process.env.SMOKE_INTEGRATION_SECRET;
assert.ok(SECRET, "需要 SMOKE_INTEGRATION_SECRET");
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-linepay-repay-shots";
mkdirSync(SHOTS, { recursive: true });

// ── 假 LINE Pay：只需要開交易、付款頁（取消）與補查 ──
const txs = new Map();
let next = 2026101599000000000n;
const fake = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${FAKE_PORT}`);
  const body = await new Promise((resolve) => {
    let data = ""; req.on("data", (c) => { data += c; }); req.on("end", () => resolve(data));
  });
  const reply = (text) => { res.writeHead(200, { "Content-Type": "application/json" }); res.end(text); };
  if (req.method === "POST" && url.pathname === "/v4/payments/request") {
    const r = JSON.parse(body);
    const id = String(++next);
    txs.set(id, { amount: r.amount, cancelUrl: r.redirectUrls.cancelUrl });
    return reply(`{"returnCode":"0000","returnMessage":"Success.","info":{"transactionId":${id},` +
      `"paymentUrl":{"web":"http://127.0.0.1:${FAKE_PORT}/pay/${id}","app":"line://pay/${id}"}}}`);
  }
  const page = /^\/pay\/(\d+)$/.exec(url.pathname);
  if (page) {
    const tx = txs.get(page[1]);
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    return res.end(`<!doctype html><meta name="viewport" content="width=device-width"><h1>假 LINE Pay</h1>` +
      `<p>NT$${tx.amount}</p><a id="cancel" href="${tx.cancelUrl}">取消</a>`);
  }
  if (/^\/v4\/payments\/requests\/\d+\/check$/.test(url.pathname)) return reply('{"returnCode":"0000","returnMessage":"ok"}');
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
  const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const starts = [];
  guest.on("response", (r) => {
    if (/\/api\/orders\/[^/]+\/linepay$/.test(new URL(r.url()).pathname)) starts.push(r.status());
  });
  // 名額依整分鐘計：離下一分鐘太近就先等，整段流程才會落在同一分鐘、真的壓到名額邊界。
  const sec = new Date().getSeconds();
  if (sec > 30) await new Promise((resolve) => setTimeout(resolve, (61 - sec) * 1000));
  await guest.goto(ORDER);
  await guest.locator(".item").filter({ hasText: "拿鐵" }).first().locator(".item-add").click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  await guest.getByLabel(/LINE Pay/).check();
  await guest.getByRole("button", { name: "用 LINE Pay 付款" }).click();

  // 第一次付款：建單時已驗證，直接到付款頁；客人按取消
  await guest.waitForURL(new RegExp(`127\\.0\\.0\\.1:${FAKE_PORT}/pay/\\d+$`), { timeout: 90000 });
  await guest.locator("#cancel").click();
  await guest.getByText("LINE Pay 付款已取消，沒有扣款。").waitFor({ timeout: 30000 });
  await guest.screenshot({ path: `${SHOTS}/01-cancelled.png` });
  assert.deepEqual(starts, [200]);

  // 同網路再用掉 3 個付款名額（共 4 個；每分鐘 5 個）：亂打的付款請求一樣會被計數
  for (let i = 0; i < 3; i++) {
    const r = await fetch(`${ORDER}/api/orders/${"x".repeat(43)}/linepay`, { method: "POST" });
    assert.equal(r.status, 404);
  }
  // 重新付款：訂單頁說要驗證 → 先驗證、只送一次 → 用最後一個名額就成功（Codex 審查：不可空送一次去試）
  await guest.getByRole("button", { name: "用 LINE Pay 付款" }).click();
  await guest.waitForURL(new RegExp(`127\\.0\\.0\\.1:${FAKE_PORT}/pay/\\d+$`), { timeout: 90000 });
  assert.deepEqual(starts, [200, 200], `start responses: ${starts}`);
  assert.equal(txs.size, 2, "re-pay opens exactly one new LINE Pay transaction");
  await guest.screenshot({ path: `${SHOTS}/02-repay-page.png` });
  const sixth = await fetch(`${ORDER}/api/orders/${"x".repeat(43)}/linepay`, { method: "POST" });
  assert.equal(sixth.status, 429, "quota really was at its 5-per-minute boundary");
  console.log("PASS first pay → cancel → 4/5 payment slots used → re-pay verifies first, one request, 200 → 2nd tx; 6th → 429");
} finally {
  await browser.close();
  fake.close();
}
