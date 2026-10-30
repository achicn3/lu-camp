// 採購單日期與付款條件瀏覽器煙霧（docs/99 第一期 1B）：
// 管理者建供應商時設「月結每月 10 號」→ 建採購單帶實際下單日（昨天）與預計到貨日 →
// 明細的「日期與付款」→ 把預計到貨改成昨天 → 行事曆與首頁出現「逾期未到」→
// 收貨後月結期限算出來（下個月 10 號）、行事曆出現「到貨」。
//
// 斷言攔到的 request 與後端實際狀態，不是只看畫面文字。需 backend + frontend 已起；SMOKE_ALLOW_WRITE=1。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = join(process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots"), "purchasing-dates");
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立供應商與採購單，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const RUN = String(Date.now()).slice(-5);
const SUPPLIER = `月結批發-${RUN}`;
const PRODUCT = `日期煙霧瓦斯-${RUN}`;

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei" }).format(new Date());
const shift = (iso, days) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};
const slash = (iso) => iso.replaceAll("-", "/");
const yesterday = shift(today, -1);
const nextMonth10 = (() => {
  const [y, m] = today.split("-").map(Number);
  const [ny, nm] = m === 12 ? [y + 1, 1] : [y, m + 1];
  return `${ny}-${String(nm).padStart(2, "0")}-10`;
})();

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1366, height: 1000 } });
const page = await context.newPage();
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
const writes = [];
page.on("request", (req) => {
  if (["POST", "PATCH"].includes(req.method()) && /\/api\/v1\/(suppliers|purchase-orders)/.test(req.url())) {
    let body = null;
    try {
      body = JSON.parse(req.postData() ?? "null");
    } catch {
      body = null;
    }
    writes.push({ method: req.method(), url: req.url(), body });
  }
});

let headers;
async function api(path, init = {}) {
  const res = await context.request.fetch(`${API}${path}`, { headers, ...init });
  assert.ok(res.ok(), `${init.method ?? "GET"} ${path} 失敗：${res.status()} ${await res.text()}`);
  return res.json();
}

try {
  const login = await context.request.fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    data: { username: "dev-manager", password: PASSWORD },
  });
  assert.ok(login.ok(), `登入 API 失敗：${login.status()}`);
  headers = { Authorization: `Bearer ${(await login.json()).access_token}`, "Content-Type": "application/json" };
  await api("/api/v1/catalog-products", {
    method: "POST",
    data: { name: PRODUCT, sku: `DATES-${RUN}`, unit_price: "180", reorder_point: 0 },
  });

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15000 });

  // 1) 供應商：管理者設月結每月 10 號
  await page.goto(`${BASE}/purchasing?tab=suppliers`, { waitUntil: "networkidle" });
  await page.getByLabel("供應商名稱").fill(SUPPLIER);
  await page.getByLabel("付款條件", { exact: true }).selectOption("MONTHLY");
  await page.getByLabel("每月幾號付").fill("10");
  await page.getByRole("button", { name: "新增供應商" }).click();
  await page.locator("tr", { hasText: SUPPLIER }).waitFor({ timeout: 10000 });
  const supplierPost = writes.find((w) => w.method === "POST" && w.url.endsWith("/suppliers"));
  ok(
    "新增供應商送出月結條件",
    supplierPost?.body?.payment_terms === "MONTHLY" && supplierPost?.body?.payment_terms_value === 10,
    JSON.stringify(supplierPost?.body),
  );
  ok(
    "供應商清單顯示付款條件",
    (await page.locator("tr", { hasText: SUPPLIER }).textContent())?.includes("月結（每月 10 號付上個月的）") ?? false,
  );

  // 2) 建採購單：實際下單日昨天（電話先下單、今天才補登）、預計到貨後天
  await page.goto(`${BASE}/purchasing/new`, { waitUntil: "networkidle" });
  const supplierInput = page.getByLabel("供應商", { exact: true });
  await supplierInput.click();
  await supplierInput.fill(SUPPLIER);
  await page.getByRole("option", { name: SUPPLIER, exact: true }).click();
  await page.getByLabel("搜尋一般商品").fill(PRODUCT);
  await page.getByRole("button", { name: new RegExp(PRODUCT) }).click();
  await page.getByLabel(`進貨單價 ${PRODUCT}`).fill("100");
  await page.getByLabel("預計到貨日（選填）").fill(shift(today, 2));
  await page.getByLabel("實際下單日（送出時）").fill(yesterday);
  await page.getByRole("button", { name: "送出採購" }).click();
  await page.waitForURL(/\/purchasing\/\d+$/, { timeout: 15000 });
  const poId = Number(new URL(page.url()).pathname.split("/").pop());
  const created = writes.find((w) => w.method === "POST" && w.url.endsWith("/purchase-orders"));
  ok(
    "建單送出實際下單日與預計到貨日",
    created?.body?.ordered_on === yesterday && created?.body?.expected_on === shift(today, 2),
    JSON.stringify({ ordered_on: created?.body?.ordered_on, expected_on: created?.body?.expected_on }),
  );

  const schedule = page.locator(".pur-schedule");
  await schedule.waitFor({ timeout: 10000 });
  const scheduleText = (await schedule.textContent()) ?? "";
  ok("明細顯示實際下單日（昨天）", scheduleText.includes(slash(yesterday)), scheduleText);
  ok("明細顯示送出時快照的付款條件", scheduleText.includes("月結（每月 10 號付上個月的）"));
  ok("月結還沒到貨：付款期限還算不出來", scheduleText.includes("還算不出來"));

  // 3) 廠商說貨晚到？這裡改成「昨天就該到」做出逾期未到
  const expectedRow = schedule.locator("div", { has: page.getByText("預計到貨日", { exact: true }) });
  await expectedRow.getByRole("button", { name: "改" }).click();
  await expectedRow.getByLabel("預計到貨日").fill(yesterday);
  await expectedRow.getByRole("button", { name: "儲存" }).click();
  await schedule.getByText("（逾期未到）").waitFor({ timeout: 10000 });
  ok("改預計到貨後標出逾期未到", true);
  await page.screenshot({ path: join(SHOTS, "01-detail-schedule.png"), fullPage: true });

  // 4) 行事曆：昨天的格子有「下單」與紅色的「預計到貨」
  await page.goto(`${BASE}/calendar?day=${yesterday}`, { waitUntil: "networkidle" });
  const monthRadio = page.getByRole("radio", { name: "月曆" });
  if ((await monthRadio.getAttribute("aria-checked")) !== "true") await monthRadio.click();
  const cell = page.locator(".agenda-cell", { has: page.getByText(`預計到貨：${SUPPLIER}`) });
  await cell.waitFor({ timeout: 10000 });
  ok("月曆格子有逾期的預計到貨（紅色）", (await cell.locator(".agenda-tone-overdue", { hasText: SUPPLIER }).count()) === 1);
  ok("月曆格子有下單日", (await cell.getByText(`下單：${SUPPLIER}`).count()) === 1);
  await page.screenshot({ path: join(SHOTS, "02-calendar-purchase.png"), fullPage: true });

  // 5) 首頁「今天」：過了預計日還沒到
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const panel = page.getByRole("region", { name: "今天" });
  await panel.getByText("過了預計日還沒到").waitFor({ timeout: 10000 });
  ok(
    "首頁列出過了預計日還沒到、點了到那張採購單",
    (await panel.getByRole("link", { name: `預計到貨：${SUPPLIER}` }).getAttribute("href")) === `/purchasing/${poId}`,
  );
  await page.screenshot({ path: join(SHOTS, "03-home-late-delivery.png") });

  // 6) 收貨：月結期限算出來；行事曆出現到貨、逾期消失
  await page.goto(`${BASE}/purchasing/${poId}`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "收貨入庫" }).click();
  await page.getByRole("dialog", { name: "確認收貨" }).waitFor();
  await page.getByRole("button", { name: "確認收貨" }).click();
  await page.locator("span.inv-badge", { hasText: "已收貨" }).waitFor({ timeout: 10000 });
  await schedule.getByText(slash(nextMonth10)).waitFor({ timeout: 10000 });
  const po = await api(`/api/v1/purchase-orders/${poId}`);
  ok("收貨後月結期限＝下個月 10 號", po.payment_due_on === nextMonth10, po.payment_due_on);

  const todayInfo = await api("/api/v1/agenda/today");
  ok(
    "收齊之後不再算逾期未到",
    !todayInfo.deliveries_late.some((i) => i.purchase_order_id === poId),
  );
  const feed = await api(`/api/v1/agenda/items?start=${today}&end=${today}`);
  ok(
    "行事曆今天有到貨",
    feed.items.some((i) => i.kind === "PO_RECEIVED" && i.purchase_order_id === poId),
  );

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (err) {
  ok("流程中斷", false, String(err));
  await page.screenshot({ path: join(SHOTS, "99-error.png"), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const passed = results.filter((r) => r.pass).length;
console.log(`\n${passed}/${results.length} 通過；截圖在 ${SHOTS}`);
process.exit(passed === results.length ? 0 : 1);
