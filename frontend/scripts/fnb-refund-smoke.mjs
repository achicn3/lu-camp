// 餐飲交易紀錄＋餐點退款瀏覽器煙霧（docs/47）：
// 賣出拿鐵×2＋戚風×1（拿鐵每日限量 5 份）→ /fnb-sales 看得到這筆 → 退 1 杯拿鐵並勾「這份還能賣」
// → 退款去向顯示現金 150 → 送出 → 清單已退 150、後端份數加回、送出的 body 帶 resellable。
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
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立品項、銷售與退款，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const run = randomUUID().slice(0, 6);
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
const page = await context.newPage();
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));
let returnBody = null;
page.on("request", (req) => {
  if (req.method() === "POST" && /\/api\/v1\/returns$/.test(req.url())) {
    returnBody = JSON.parse(req.postData() ?? "{}");
  }
});

let token = "";
async function api(method, path, body, extraHeaders = {}) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...extraHeaders },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
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

  const cash = await api("GET", "/api/v1/cash-sessions/current");
  if (cash.status !== 200 || cash.body === null) {
    await api("POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  }
  const latte = await api("POST", "/api/v1/menu-items", { name: `拿鐵-${run}`, unit_price: "150" });
  const cake = await api("POST", "/api/v1/menu-items", { name: `戚風-${run}`, unit_price: "90" });
  await api("PATCH", `/api/v1/menu-items/${latte.body.id}`, { daily_limited: true });
  await api("POST", `/api/v1/menu-daily-stock/item/${latte.body.id}/set`, {
    qty: 5,
    expected_remaining: 0,
  });
  const sale = await api(
    "POST",
    "/api/v1/sales",
    {
      lines: [
        { line_type: "MENU", menu_item_id: latte.body.id, qty: 2 },
        { line_type: "MENU", menu_item_id: cake.body.id, qty: 1 },
      ],
      service_mode: "TAKEOUT",
    },
    { "Idempotency-Key": `fnb-${run}` },
  );
  ok("賣出拿鐵×2＋戚風×1", sale.status === 201, `HTTP ${sale.status}`);
  const saleId = sale.body.id;

  // 1. 餐飲交易紀錄看得到這筆
  await page.goto(`${BASE}/fnb-sales`, { waitUntil: "networkidle" });
  const row = page.locator("tr", { hasText: `#${saleId}` });
  await row.waitFor();
  const rowText = await row.textContent();
  ok("清單列出這筆與餐點摘要", rowText.includes(`拿鐵-${run}×2`) && rowText.includes("$390"), rowText);
  await page.screenshot({ path: `${SHOTS}/fnb-01-list.png`, fullPage: true });

  // 2. 退 1 杯拿鐵、勾還能賣
  await row.getByRole("button", { name: `餐點退款 ${saleId}` }).click();
  const dialog = page.getByRole("dialog", { name: "餐點退款" });
  await dialog.waitFor();
  const qty = dialog.getByLabel(`拿鐵-${run} 退貨數量`);
  await qty.fill("1");
  await dialog.getByLabel(`拿鐵-${run} 這份還能賣`).check();
  await dialog.getByLabel("退貨原因").fill("太甜");
  const legs = dialog.getByLabel("預估退款去向");
  await legs.waitFor();
  ok("退款去向顯示現金 150", (await legs.textContent()).includes("150"), await legs.textContent());
  await page.screenshot({ path: `${SHOTS}/fnb-02-dialog.png`, fullPage: true });
  await dialog.getByRole("button", { name: "確認退款 $150" }).click();
  await page.getByRole("status").filter({ hasText: "已退款" }).waitFor();
  ok(
    "送出帶 resellable",
    JSON.stringify(returnBody?.lines) ===
      JSON.stringify([{ sale_line_id: sale.body.lines[0].id, qty: 1, resellable: true }]),
    JSON.stringify(returnBody?.lines),
  );

  // 3. 清單與後端狀態
  await page.locator("tr", { hasText: `#${saleId}` }).getByText("$150").first().waitFor();
  const fnb = await api("GET", `/api/v1/sales/fnb?sale_id=${saleId}`);
  const mine = fnb.body.find((r) => r.id === saleId);
  ok("後端已退 150（餐點）", mine?.food_refunded === "150", JSON.stringify(mine));
  const stock = await api("GET", "/api/v1/menu-daily-stock");
  const left = stock.body.find((e) => e.kind === "item" && e.id === latte.body.id);
  ok("勾了還能賣：份數加回（5−2+1=4）", left?.remaining === 4, JSON.stringify(left));
  await page.screenshot({ path: `${SHOTS}/fnb-03-refunded.png`, fullPage: true });
} catch (err) {
  ok("流程執行", false, String(err));
  await page.screenshot({ path: `${SHOTS}/fnb-error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
