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
await integration("PUT", "/integration/linepay", JSON.stringify({ enabled: true })); // 線上 LINE Pay 開關預設關（2026-10-20）
await integration("GET", "/integration/orders");

const browser = await chromium.launch();
try {
  const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  // 換成假的 Turnstile 小工具：發 Cloudflare 測試金鑰收的假 token（雲端照樣向 siteverify 驗證），
  // 設了 window.__challengeDelay 就延後發——模擬驗證慢的手機，讓客人有時間在等驗證時連按（Codex 審查第二輪）。
  await guest.route(/challenges\.cloudflare\.com\/turnstile\/v0\/api\.js/, (route) => route.fulfill({
    contentType: "text/javascript",
    body: `(() => {
      let opts = null;
      const issue = () => setTimeout(() => opts.callback("XXXX.DUMMY.TOKEN.XXXX"), window.__challengeDelay ?? 300);
      window.turnstile = { render(el, o) { opts = o; issue(); return "w1"; }, reset() { issue(); } };
      window.onTurnstileReady?.();
    })();`,
  }));
  const starts = [];
  guest.on("response", (r) => {
    if (/\/api\/orders\/[^/]+\/linepay$/.test(new URL(r.url()).pathname)) starts.push(r.status());
  });
  // 名額依整分鐘計：一律等到下一分鐘開頭，整段流程才會落在同一分鐘、真的壓到名額邊界，
  // 也不會吃到上一次執行在同一分鐘用掉的名額。
  await new Promise((resolve) => setTimeout(resolve, (61 - new Date().getSeconds()) * 1000));
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
  // 重新付款：訂單頁說要驗證 → 先驗證、只送一次 → 用最後一個名額就成功（Codex 審查：不可空送一次去試）。
  // 驗證要 7 秒：等待中連按、訂單頁輪詢（5 秒）重畫後再按，都不可多送付款請求（Codex 審查第二輪）。
  await guest.evaluate(() => { window.__challengeDelay = 7000; });
  const repay = guest.getByRole("button", { name: "用 LINE Pay 付款" });
  await repay.click();
  await repay.click({ force: true, timeout: 2000 }).catch(() => {});
  await guest.waitForTimeout(5500);
  assert.ok(await repay.isDisabled(), "re-pay button stays disabled while verification is pending, even after the poll redraw");
  await guest.screenshot({ path: `${SHOTS}/02a-repay-verifying.png` });
  await repay.click({ force: true, timeout: 2000 }).catch(() => {});
  await guest.waitForURL(new RegExp(`127\\.0\\.0\\.1:${FAKE_PORT}/pay/\\d+$`), { timeout: 90000 });
  assert.deepEqual(starts, [200, 200], `start responses: ${starts}`);
  assert.equal(txs.size, 2, "re-pay opens exactly one new LINE Pay transaction");
  await guest.waitForTimeout(1500); // 連按若多送了，晚到的請求會在這段時間出現
  assert.deepEqual(starts, [200, 200], `no extra start requests after navigation: ${starts}`);
  await guest.screenshot({ path: `${SHOTS}/02-repay-page.png` });
  const sixth = await fetch(`${ORDER}/api/orders/${"x".repeat(43)}/linepay`, { method: "POST" });
  assert.equal(sixth.status, 429, "quota really was at its 5-per-minute boundary");
  console.log("PASS first pay → cancel → 4/5 payment slots used → re-pay verifies first (7s, clicked 3×, poll redraw), one request, 200 → 2nd tx; 6th → 429");
} finally {
  await browser.close();
  fake.close();
}
