// 餐飲每日限量瀏覽器煙霧（docs/44 §3.7，2026-10-01 裁示）：
// 菜單頁勾「每日限量」→ 開店檢查出現「今日餐點數量」待處理 → 在檢查頁填份數 → +1／−1（選報廢）
// → POS 磚顯示剩幾份 → 賣完後磚變售完、不能點 → 再賣被後端擋下。
//
// 斷言攔到的 request body 與後端實際狀態，不是只看畫面文字。
// 需 backend + frontend 已起，且指向隔離測試庫（SMOKE_ALLOW_WRITE=1）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立品項與銷售，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const run = randomUUID().slice(0, 6);
const cakeName = `戚風-${run}`;
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
const page = await context.newPage();
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

const writes = [];
page.on("request", (req) => {
  if (!["POST", "PATCH"].includes(req.method())) return;
  if (!/\/api\/v1\/(menu-items|menu-daily-stock)/.test(req.url())) return;
  try {
    writes.push({ url: req.url(), body: JSON.parse(req.postData() ?? "{}") });
  } catch {
    writes.push({ url: req.url(), body: null });
  }
});

let token = "";
async function api(method, path, body, extraHeaders = {}) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...extraHeaders,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
}

async function remainingOf(itemId) {
  const { body } = await api("GET", "/api/v1/menu-daily-stock");
  return body.find((e) => e.kind === "item" && e.id === itemId);
}

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
  ok("登入成功", Boolean(token));

  // 準備：開帳（若尚未開）＋建一個甜點品項。
  const cash = await api("GET", "/api/v1/cash-sessions/current");
  if (cash.status !== 200 || cash.body === null) {
    const opened = await api("POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
    ok("開帳", opened.status === 201 || opened.status === 200, `HTTP ${opened.status}`);
  }
  const created = await api("POST", "/api/v1/menu-items", {
    name: cakeName,
    unit_price: "90",
    category: "甜點",
  });
  ok("建立甜點品項", created.status === 201, `HTTP ${created.status}`);
  const cakeId = created.body.id;

  // 1. 菜單頁勾「每日限量」
  await page.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  // 勾選框等後端存好才打勾（不是樂觀更新），所以用 click 再等畫面跟上。
  await page.getByLabel(`${cakeName} 每日限量`).click();
  await page.waitForFunction(
    (name) =>
      [...document.querySelectorAll("tr")]
        .find((r) => r.textContent?.includes(name))
        ?.textContent?.includes("今天未填份數") ?? false,
    cakeName,
    { timeout: 10000 },
  );
  const patch = writes.find((w) => w.url.endsWith(`/menu-items/${cakeId}`));
  ok("勾選送出 daily_limited=true", patch?.body?.daily_limited === true, JSON.stringify(patch?.body));
  await page.locator(`tr:has-text("${cakeName}")`).screenshot({ path: `${SHOTS}/dl-01-menu-checkbox.png` });

  // 2. 開店檢查：今日餐點數量待處理，直接在這裡填 3 份
  await page.goto(`${BASE}/opening-check`, { waitUntil: "networkidle" });
  const autoRow = page.locator("li.opening-item", { hasText: "今日餐點數量" });
  await autoRow.waitFor();
  ok("開店檢查出現「今日餐點數量」待處理", (await autoRow.textContent()).includes("待處理"));
  await page.screenshot({ path: `${SHOTS}/dl-02-opening-pending.png`, fullPage: true });

  const stockRow = page.locator("li.daily-stock-row", { hasText: cakeName });
  await stockRow.getByLabel(`${cakeName} 今日份數`).fill("3");
  await stockRow.getByRole("button", { name: "設定" }).click();
  await stockRow.getByText("今天剩 3 份").waitFor();
  const setCall = writes.find((w) => w.url.includes(`/menu-daily-stock/item/${cakeId}/set`));
  ok(
    "設定送出 qty=3、expected_remaining=0",
    setCall?.body?.qty === 3 && setCall?.body?.expected_remaining === 0,
    JSON.stringify(setCall?.body),
  );

  // 3. +1 → 4；−1 選報廢 → 3
  await stockRow.getByRole("button", { name: `${cakeName} 加一份` }).click();
  await page.locator("li.daily-stock-row", { hasText: cakeName }).getByText("今天剩 4 份").waitFor();
  const row2 = page.locator("li.daily-stock-row", { hasText: cakeName });
  await row2.getByRole("button", { name: `${cakeName} 減一份` }).click();
  await row2.getByRole("button", { name: "報廢" }).click();
  await page.locator("li.daily-stock-row", { hasText: cakeName }).getByText("今天剩 3 份").waitFor();
  const adjusts = writes.filter((w) => w.url.includes(`/menu-daily-stock/item/${cakeId}/adjust`));
  ok(
    "+1 送補貨、−1 送報廢",
    JSON.stringify(adjusts.map((a) => a.body)) ===
      JSON.stringify([{ delta: 1, reason: "RESTOCK" }, { delta: -1, reason: "WASTE" }]),
    JSON.stringify(adjusts.map((a) => a.body)),
  );
  ok("後端份數為 3", (await remainingOf(cakeId))?.remaining === 3);
  await page.screenshot({ path: `${SHOTS}/dl-03-opening-filled.png`, fullPage: true });

  // 4. POS 磚顯示剩 3 份
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  const tile = page.locator(".pos-menu-tile", { hasText: cakeName });
  await tile.waitFor();
  ok("POS 磚顯示剩 3 份", (await tile.textContent()).includes("剩 3 份"));
  await page.locator(".pos-menu").screenshot({ path: `${SHOTS}/dl-04-pos-remaining.png` });

  // 5. 賣掉 3 份（API）→ 重新整理 POS：售完、不能點
  const sale = await api(
    "POST",
    "/api/v1/sales",
    {
      lines: [{ line_type: "MENU", menu_item_id: cakeId, qty: 3 }],
      service_mode: "TAKEOUT",
    },
    { "Idempotency-Key": `dl-${run}-1` },
  );
  ok("賣出 3 份", sale.status === 201, `HTTP ${sale.status} ${JSON.stringify(sale.body?.detail ?? "")}`);
  await page.reload({ waitUntil: "networkidle" });
  const soldOut = page.locator(".pos-menu-tile", { hasText: cakeName });
  await soldOut.waitFor();
  ok("POS 磚顯示售完", (await soldOut.textContent()).includes("售完"));
  ok("售完的磚不能點", await soldOut.isDisabled());
  await page.locator(".pos-menu").screenshot({ path: `${SHOTS}/dl-05-pos-soldout.png` });

  // 6. 再賣一份被後端擋下（不是只靠畫面停用）
  const again = await api(
    "POST",
    "/api/v1/sales",
    {
      lines: [{ line_type: "MENU", menu_item_id: cakeId, qty: 1 }],
      service_mode: "TAKEOUT",
    },
    { "Idempotency-Key": `dl-${run}-2` },
  );
  ok(
    "售完後再賣被擋（409、訊息寫已售完）",
    again.status === 409 && String(again.body?.detail).includes("已售完"),
    `HTTP ${again.status} ${again.body?.detail}`,
  );
} catch (err) {
  ok("流程執行", false, String(err));
  await page.screenshot({ path: `${SHOTS}/dl-error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
