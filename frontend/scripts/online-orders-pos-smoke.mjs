// POS 線上訂單瀏覽器煙霧（docs/44 §4.3；O4b＋O4c）：真雲端（本機 wrangler dev）＋真 backend＋真 Postgres。
// 發佈菜單（拿鐵不限量、戚風每日限量 2 份）→ 客人用桌位碼向雲端送現金單 → POS 背景拉單（5 秒內）、
// 徽章出現 → 戚風保留（剩 1 份）→ 「帶入結帳」品項／桌號帶進購物車 → 收現結帳 → 雲端那張單變已付款、
// 份數只扣一次 → 第二張要 5 份戚風被拒（庫存不足）→ 第三張取消、雲端變已取消 → 暫停接單、恢復。
//
// 需三個服務已起且指向隔離測試庫（SMOKE_ALLOW_WRITE=1）：backend（ONLINE_ORDER_BASE_URL 指向 SMOKE_ORDER、
// 密鑰與 wrangler 的 .dev.vars 相同）、frontend、wrangler dev（.dev.vars 的 TURNSTILE_SECRET 用 Cloudflare 測試用
// 的「一律通過」密鑰）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const ORDER = process.env.SMOKE_ORDER ?? "http://localhost:8787";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "online-orders-pos");
// Cloudflare Turnstile 測試用 token（搭配「一律通過」的測試密鑰）。
const TURNSTILE_TEST_TOKEN = "XXXX.DUMMY.TOKEN.XXXX";
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立品項、發佈到線上並結帳，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

let token = "";
async function api(method, path, body) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
}

/** 客人送單（每張用不同的裝置 cookie，免得撞「同一裝置未付款上限」）。 */
async function placeOrder(tableCode, lines, note = "") {
  const resp = await fetch(`${ORDER}/api/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Cookie: `lk_dev=${randomUUID().replaceAll("-", "")}` },
    body: JSON.stringify({
      idempotency_key: randomUUID(),
      table_code: tableCode,
      payment_method: "CASH",
      turnstile_token: TURNSTILE_TEST_TOKEN,
      note,
      lines,
    }),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
}

async function customerView(orderToken) {
  const resp = await fetch(`${ORDER}/api/orders/${orderToken}`);
  return resp.json();
}

async function waitFor(fn, label, timeoutMs = 20000) {
  const until = Date.now() + timeoutMs;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > until) throw new Error(`等不到：${label}`);
    await new Promise((r) => setTimeout(r, 500));
  }
}

const run = randomUUID().slice(0, 6);
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
  await api("POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });

  // 菜單：拿鐵不限量、戚風每日限量 2 份；桌號 A1；發佈
  const latte = (await api("POST", "/api/v1/menu-items", { name: `拿鐵-${run}`, unit_price: "150", category: "咖啡" })).body;
  const cake = (await api("POST", "/api/v1/menu-items", { name: `戚風-${run}`, unit_price: "90", category: "甜點" })).body;
  await api("PATCH", `/api/v1/menu-items/${cake.id}`, { daily_limited: true });
  await api("POST", `/api/v1/menu-daily-stock/item/${cake.id}/set`, { qty: 2, expected_remaining: 0 });
  const settings = (await api("GET", "/api/v1/settings")).body;
  await api("PATCH", "/api/v1/settings", { dine_in_tables: [...new Set([...(settings.dine_in_tables ?? []), "A1"])] });
  const published = await api("POST", "/api/v1/online-order/publish");
  ok("發佈菜單到雲端", published.status === 200, JSON.stringify(published.body?.detail ?? ""));
  const status = (await api("GET", "/api/v1/online-order/status")).body;
  const tableCode = status.tables.find((t) => t.label === "A1")?.code;
  // 確保接單中（前一次煙霧可能留下暫停）
  await api("PUT", "/api/v1/online-orders/accepting", { accepting: true });

  // ① 客人在 A1 點拿鐵×1＋戚風×1（戚風限量 → 等 POS 確認庫存）
  const first = await placeOrder(
    tableCode,
    [
      { item_id: latte.id, option_ids: [], qty: 1 },
      { item_id: cake.id, option_ids: [], qty: 1 },
    ],
    "拿鐵少冰",
  );
  ok("客人送單成功", first.status === 201 || first.status === 200, `${first.status} ${JSON.stringify(first.body)}`);
  const firstToken = first.body.token;
  const held = await waitFor(async () => {
    const v = await customerView(firstToken);
    return v.status === "UNPAID" ? v : null;
  }, "POS 確認庫存（HELD）");
  ok("POS 拉到單並保留份數：客人那邊變待付款", held.status === "UNPAID");
  const stock = (await api("GET", "/api/v1/menu-daily-stock")).body.find((s) => s.id === cake.id);
  ok("戚風剩 1 份（已保留 1 份）", stock?.remaining === 1, JSON.stringify(stock));

  // ② POS：徽章、清單、帶入結帳
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  const toggle = page.getByRole("button", { name: /線上訂單/ });
  await toggle.waitFor();
  await page.waitForFunction(() => document.querySelector(".online-orders-badge")?.textContent !== undefined, null, { timeout: 15000 });
  ok("POS 有線上訂單徽章", (await page.locator(".online-orders-badge").innerText()).trim().length > 0);
  await toggle.click();
  const sheet = page.getByRole("dialog", { name: "線上訂單" });
  const row = sheet.getByRole("listitem", { name: /桌號 A1 \$240/ }).first();
  await row.waitFor();
  const rowText = await row.innerText();
  ok("清單：桌號、品項、合計、已保留、備註", rowText.includes(`拿鐵-${run} ×1`) && rowText.includes("已保留份數") && rowText.includes("拿鐵少冰"), rowText.replace(/\n/g, " "));
  await page.screenshot({ path: join(SHOTS, "01-online-orders.png") });
  await row.getByRole("button", { name: "帶入結帳" }).click();
  await sheet.waitFor({ state: "detached" });
  await page.getByText("正在結線上單（桌號 A1）").waitFor();
  ok("橫幅寫出客人備註", (await page.locator(".pos-online-banner").innerText()).includes("客人備註：拿鐵少冰"));
  ok("帶入購物車：兩個品項", (await page.getByText(`拿鐵-${run}`).count()) > 0 && (await page.getByText(`戚風-${run}`).count()) > 0);
  ok("內用桌號 A1 已選好", (await page.locator('.pos-dinein-table[aria-checked="true"]').innerText()).includes("A1"));
  await page.screenshot({ path: join(SHOTS, "02-loaded-cart.png") });
  await page.waitForSelector(".pos-checkout:not([disabled])", { timeout: 15000 });
  await page.click(".pos-checkout");
  await page.waitForSelector(".pos-complete", { timeout: 30000 });
  ok("結帳成立", true);
  const paid = await waitFor(async () => {
    const v = await customerView(firstToken);
    return v.status === "PAID" ? v : null;
  }, "雲端那張單變已付款");
  ok("雲端：客人那邊變已付款", paid.status === "PAID");
  const after = (await api("GET", "/api/v1/menu-daily-stock")).body.find((s) => s.id === cake.id);
  ok("戚風仍剩 1 份（保留轉成正式扣減，只扣一次）", after?.remaining === 1, JSON.stringify(after));

  // ③ 雲端照發佈時的份數判斷：明顯不夠的直接擋（不用等 POS）
  const tooMany = await placeOrder(tableCode, [{ item_id: cake.id, option_ids: [], qty: 5 }]);
  ok("要 5 份（發佈時只有 2 份）：雲端直接擋下", tooMany.status === 422 && tooMany.body?.error === "sold_out", JSON.stringify(tooMany.body));
  // 雲端以為還有、POS 其實沒了（店員剛報廢最後一份）：POS 拉到時拒絕
  await api("POST", `/api/v1/menu-daily-stock/item/${cake.id}/adjust`, { delta: -1, reason: "WASTE" });
  const second = await placeOrder(tableCode, [{ item_id: cake.id, option_ids: [], qty: 1 }]);
  ok("雲端以為還有：先收下等 POS 確認", second.status === 201 && second.body?.status === "HOLD_REQUESTED", JSON.stringify(second.body));
  const rejected = await waitFor(async () => {
    const v = await customerView(second.body.token);
    return v.status === "REJECTED" ? v : null;
  }, "POS 拒絕庫存不足的單");
  ok("POS 發現沒了：客人那邊顯示被拒", rejected.status === "REJECTED");

  // ④ 第三張取消
  const third = await placeOrder(tableCode, [{ item_id: latte.id, option_ids: [], qty: 2 }]);
  const thirdToken = third.body.token;
  await page.click('button:has-text("下一筆")').catch(() => {});
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /線上訂單/ }).click();
  const thirdRow = page.getByRole("dialog", { name: "線上訂單" }).getByRole("listitem", { name: /桌號 A1 \$300/ }).first();
  await thirdRow.waitFor({ timeout: 15000 });
  await thirdRow.getByRole("button", { name: "取消這張" }).click();
  await thirdRow.getByRole("button", { name: "確定取消" }).click();
  const cancelled = await waitFor(async () => {
    const v = await customerView(thirdToken);
    return v.status === "CANCELLED" ? v : null;
  }, "雲端那張單變已取消");
  ok("取消：雲端那張單變已取消", cancelled.status === "CANCELLED");

  // ⑤ 暫停／恢復接單
  const dialog = page.getByRole("dialog", { name: "線上訂單" });
  await dialog.getByRole("button", { name: "暫停接單" }).click();
  await dialog.getByText("暫停接單中").waitFor();
  const refused = await placeOrder(tableCode, [{ item_id: latte.id, option_ids: [], qty: 1 }]);
  ok("暫停中客人送不出單", refused.status >= 400, `${refused.status} ${JSON.stringify(refused.body)}`);
  await page.screenshot({ path: join(SHOTS, "03-paused.png") });
  await dialog.getByRole("button", { name: "恢復接單" }).click();
  await dialog.getByText("接單中").first().waitFor();
  ok("恢復接單", true);

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  console.log(String(error));
  process.exitCode = 1;
} finally {
  await browser.close();
}
