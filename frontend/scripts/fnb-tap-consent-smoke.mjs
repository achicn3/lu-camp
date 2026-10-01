// 餐點退款「點選同意」瀏覽器煙霧（docs/47 E3）：真 backend＋真 Postgres＋兩個瀏覽器（店員／顧客螢幕）。
//
// 本機沒有 Amego 憑證，發票無法真的開立：以 SQL 把**本次自己建的那張**發票標成已開立，
// 再走真 UI——餐飲交易紀錄 → 餐點退款 → 推送點選同意 → 顧客螢幕只有「我同意」、沒有簽名板 →
// 客人按同意 → 店員送出 → 開立折讓。腳本開頭打開電子發票、結尾還原（不留設定殘留）。
//
// 只能對本機拋棄式環境執行（SMOKE_ALLOW_WRITE=1、SMOKE_DB_NAME 指向測試庫）。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
const DB_CONTAINER = process.env.SMOKE_DB_CONTAINER ?? "lu-camp-db-1";
const DB_NAME = process.env.SMOKE_DB_NAME;
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建單、退款、改設定，請指向隔離測試庫");
assert.ok(DB_NAME, "請設定 SMOKE_DB_NAME（要佈置發票狀態的測試庫）");
for (const url of [BASE, API]) {
  assert.ok(["localhost", "127.0.0.1"].includes(new URL(url).hostname), `拒絕執行：${url} 非本機`);
}
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}
function sql(statement) {
  return execFileSync(
    "docker",
    ["exec", DB_CONTAINER, "psql", "-U", "lucamp", "-d", DB_NAME, "-tAc", statement],
    { encoding: "utf8" },
  ).trim();
}

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

const run = randomUUID().slice(0, 6);
const browser = await chromium.launch();
const staffCtx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
const kioskCtx = await browser.newContext({ viewport: { width: 834, height: 1112 }, hasTouch: true });
const page = await staffCtx.newPage();
const kiosk = await kioskCtx.newPage();
page.on("pageerror", (err) => ok("店員頁 JS 錯誤", false, String(err)));
kiosk.on("pageerror", (err) => ok("顧客螢幕 JS 錯誤", false, String(err)));
let einvoiceBefore = null;

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', PASS);
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));

  // 佈置：電子發票打開（結束時還原）、開帳、建一杯餐點並賣出。
  // 設定列要等第一次「寫入」才建立：寫回一個現值（不改任何東西）讓它出現，再用 SQL 打開發票。
  const current = await api("GET", "/api/v1/settings");
  await api("PATCH", "/api/v1/settings", {
    print_kitchen_ticket: current.body.print_kitchen_ticket,
  });
  einvoiceBefore = sql("SELECT einvoice_enabled FROM settings LIMIT 1");
  sql("UPDATE settings SET einvoice_enabled = true");
  const cash = await api("GET", "/api/v1/cash-sessions/current");
  if (cash.status !== 200 || cash.body === null) {
    await api("POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  }
  const latte = await api("POST", "/api/v1/menu-items", { name: `拿鐵-${run}`, unit_price: "150" });
  const sale = await api(
    "POST",
    "/api/v1/sales",
    {
      lines: [{ line_type: "MENU", menu_item_id: latte.body.id, qty: 2 }],
      service_mode: "TAKEOUT",
      // 電子發票開啟時，結帳要宣告看到的發票設定（防止前端過期設定靜默開出 B2C）。
      expected_einvoice_enabled: true,
    },
    { "Idempotency-Key": `tap-${run}` },
  );
  ok("賣出拿鐵×2", sale.status === 201, `HTTP ${sale.status}`);
  const saleId = sale.body.id;
  const date = new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);
  sql(
    `UPDATE invoices SET status='ISSUED', invoice_no='TP${String(saleId).padStart(8, "0")}',` +
      ` invoice_date='${date}', print_mark=true WHERE sale_id=${saleId}`,
  );
  sql(`UPDATE sales SET invoice_status='ISSUED' WHERE id=${saleId}`);
  ok("發票已佈置為已開立", sql(`SELECT status FROM invoices WHERE sale_id=${saleId}`) === "ISSUED");

  // 顧客螢幕登入並與 POS 配對。
  await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "domcontentloaded" });
  await kiosk.waitForTimeout(1200);
  if (await kiosk.locator('input[name="username"]').count()) {
    await kiosk.fill('input[name="username"]', "dev-kiosk");
    await kiosk.fill('input[name="password"]', PASS);
    await kiosk.click('button:has-text("啟用裝置"), button:has-text("登入")');
    await kiosk.waitForTimeout(2500);
  }
  if (await kiosk.locator(".kiosk-pairing-code").count()) {
    const code = (await kiosk.textContent(".kiosk-pairing-code")).trim();
    await page.goto(`${BASE}/pos`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(".pos-kiosk-status", { timeout: 20000 });
    await page.fill(".pos-kiosk-status input", code);
    await page.click('.pos-kiosk-status button:has-text("配對")');
    await page.waitForSelector(".pos-kiosk-status.is-online", { timeout: 20000 });
  }
  await kiosk.waitForSelector(".kiosk-standby", { timeout: 25000 });
  ok("顧客螢幕已配對待命", true);

  // 餐飲交易紀錄 → 餐點退款 1 杯 → 需要同意 → 推送點選同意
  await page.goto(`${BASE}/fnb-sales`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: `餐點退款 ${saleId}` }).click();
  const dialog = page.getByRole("dialog", { name: "餐點退款" });
  await dialog.getByLabel(`拿鐵-${run} 退貨數量`).fill("1");
  await dialog.getByLabel("退貨原因").fill("咖啡是酸的");
  await dialog.getByText("請先請客人於顧客螢幕點選同意").waitFor({ timeout: 15000 });
  ok("提示要客人點選同意", true);
  await dialog.getByRole("button", { name: "請客人於顧客螢幕點選同意" }).click();

  // 顧客螢幕：只有「我同意」、沒有簽名板
  const agree = kiosk.getByRole("button", { name: "我同意" });
  await agree.waitFor({ timeout: 25000 });
  ok("顧客螢幕沒有簽名板", (await kiosk.locator("canvas.kiosk-sign-canvas").count()) === 0);
  await kiosk.screenshot({ path: `${SHOTS}/tap-01-kiosk-agree.png`, fullPage: true });
  await agree.click();
  await kiosk.getByText("已完成簽署").waitFor({ timeout: 15000 });

  // 店員端：看到客人已同意 → 送出
  await dialog.getByText(/客人已同意/).waitFor({ timeout: 20000 });
  await page.screenshot({ path: `${SHOTS}/tap-02-staff-agreed.png`, fullPage: true });
  await dialog.getByRole("button", { name: "確認退款 $150" }).click();
  await page.getByRole("status").filter({ hasText: "已退款" }).waitFor({ timeout: 15000 });
  ok("退款成立", true);

  const allowance = sql(
    `SELECT a.total FROM invoice_allowances a JOIN invoices i ON i.id=a.invoice_id WHERE i.sale_id=${saleId}`,
  );
  ok("開立折讓 150", allowance === "150", allowance);
  const consent = sql(
    `SELECT consent_mode || '/' || (signature_image IS NULL)::text || '/' || status FROM signature_tasks` +
      ` WHERE kind='RETURN_INVOICE_CONSENT' AND ref_id=${saleId} ORDER BY id DESC LIMIT 1`,
  );
  ok("同意紀錄為點選、無簽名圖、已使用", consent === "TAP/true/CONSUMED", consent);
} catch (err) {
  ok("流程執行", false, String(err));
  await page.screenshot({ path: `${SHOTS}/tap-error.png`, fullPage: true }).catch(() => {});
  await kiosk.screenshot({ path: `${SHOTS}/tap-error-kiosk.png`, fullPage: true }).catch(() => {});
} finally {
  if (einvoiceBefore !== null) {
    sql(`UPDATE settings SET einvoice_enabled = ${einvoiceBefore === "t" ? "true" : "false"}`);
  }
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
