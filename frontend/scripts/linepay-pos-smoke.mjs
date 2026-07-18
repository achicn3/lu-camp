// LINE Pay POS UI（docs/30 P3）瀏覽器煙霧：啟用 LINE Pay → POS 選 LINE Pay 收款 → 掃碼欄
// 填入真 oneTimeKey → 結帳（真沙盒真收費）→ 完成頁顯示 LINE Pay → 收尾作廢（真退款）。
// 執行：node scripts/linepay-pos-smoke.mjs（需 backend:8000 帶 LINEPAY_* env、frontend:3000）。
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import jsQR from "jsqr";
import { chromium } from "playwright";
import { PNG } from "pngjs";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = "http://localhost:3000";
const API = "http://localhost:8000";
const SANDBOX = "https://sandbox-web-pay.line.me/web/sandbox/payment/oneTimeKey?countryCode=TW";
const SHOTS = join(homedir(), "tmp", "codex-test", "linepay-pos-smoke");
mkdirSync(SHOTS, { recursive: true });

const results = [];
const ok = (name, pass, detail = "") => {
  results.push({ pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
};

async function api(path, { method = "GET", token, body, headers = {}, expect = [200] } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!expect.includes(res.status)) throw new Error(`${method} ${path} → ${res.status}: ${text}`);
  return data;
}
const psql = (sql) =>
  execSync(`docker exec lu-camp-db-1 psql -U lucamp -d lucamp_e2e -tAc "${sql}"`, {
    encoding: "utf8",
  }).trim();

async function decodeKey(page) {
  await page.goto(SANDBOX, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(1200);
  const src = await page.evaluate(() => document.querySelectorAll("img")[0]?.src || "");
  const png = PNG.sync.read(Buffer.from(src.split(",")[1], "base64"));
  const qr = jsQR(new Uint8ClampedArray(png.data), png.width, png.height);
  if (!qr) throw new Error("QR 解碼失敗");
  return qr.data.trim();
}

let browser;
try {
  // API 準備：啟用 LINE Pay、開帳、收購一件可售序號品
  const { access_token: token } = await api("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  await api("/api/v1/settings", {
    method: "PATCH",
    token,
    body: { linepay_enabled: true, linepay_fee_pct: "0.0150" },
  });
  const cur = await api("/api/v1/cash-sessions/current", { token });
  if (cur === null)
    await api("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
      expect: [201],
    });
  const runId = `${Date.now()}`;
  const seller = await api("/api/v1/contacts", {
    method: "POST",
    token,
    expect: [201],
    body: {
      name: `LP-POS賣方${runId}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
      member_points: 0,
      source_note: "linepay pos smoke",
    },
  });
  const acq = await api("/api/v1/acquisitions", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `acq-${runId}` },
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      note: "LP-POS",
      items: [{ name: `LP-POS帳篷${runId}`, grade: "A", listed_price: "600", acquisition_cost: "200" }],
    },
  });
  const code = acq.item_codes[0];
  ok("API 準備（啟用 LINE Pay＋可售品）", true, code);

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

  const oneTimeKey = await decodeKey(page);
  ok("解碼真 oneTimeKey", true, oneTimeKey);

  // 登入 POS
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector('input[name="code"]');
  await page.fill('input[name="code"]', code);
  await page.press('input[name="code"]', "Enter");
  await page.waitForSelector(`text=LP-POS帳篷${runId}`);

  // LINE Pay 選項（啟用後才出現）
  const lineRadio = page.locator(".pos-tender-mode", { hasText: "LINE Pay" });
  ok("啟用後 POS 出現 LINE Pay 收款選項", (await lineRadio.count()) === 1);
  await lineRadio.click();

  // 掃碼欄出現 + 手續費提示（折後 540 ×1.5% = 8）
  await page.waitForSelector('input[name="linepay_one_time_key"]');
  ok("LINE Pay 掃碼輸入欄出現", true);
  const feeHint = await page.locator(".pos-tender .hint", { hasText: "LINE Pay 收款" }).textContent();
  ok("手續費提示（店家負擔）", (feeHint?.includes("店家負擔") && feeHint?.includes("8")) ?? false, feeHint ?? "");

  // 未填碼 → 結帳鍵停用
  ok("未掃碼時結帳鍵停用", await page.locator('button:has-text("結帳")').isDisabled());

  // 填入真 oneTimeKey → 結帳（真收費）
  await page.fill('input[name="linepay_one_time_key"]', oneTimeKey);
  await page.screenshot({ path: `${SHOTS}/01-linepay-tender.png` });
  const checkoutBtn = page.locator('button:has-text("結帳")');
  ok("掃碼後結帳鍵啟用", !(await checkoutBtn.isDisabled()));
  await checkoutBtn.click();
  await page.waitForSelector("text=已完成", { timeout: 20000 });
  const method = page.locator(".pos-complete .stat-list dd").filter({ hasText: /^LINE Pay$/ });
  ok("結帳完成，收款方式＝LINE Pay（真沙盒 0000）", await method.isVisible());
  await page.screenshot({ path: `${SHOTS}/02-linepay-complete.png` });

  // DB 驗證：linepay_transactions COMPLETE
  const saleId = psql(
    "SELECT id FROM sales WHERE payment_method='LINE_PAY' ORDER BY id DESC LIMIT 1",
  );
  const txn = psql(
    `SELECT status || '|' || amount FROM linepay_transactions WHERE sale_id=${saleId}`,
  );
  ok("DB linepay_transactions COMPLETE", txn.startsWith("COMPLETE|"), `sale #${saleId} ${txn}`);

  // 收尾：作廢（真退款），不留沙盒殘留
  await api(`/api/v1/sales/${saleId}/void`, { method: "POST", token });
  const refunded = psql(`SELECT status FROM linepay_transactions WHERE sale_id=${saleId}`);
  ok("收尾作廢真退款 → REFUNDED", refunded === "REFUNDED", refunded);

  const failed = results.filter((r) => !r.pass);
  console.log(`\n${failed.length === 0 ? "✅ 全數通過" : `❌ ${failed.length} 失敗`}（${results.length} 檢查）`);
  console.log(`截圖：${SHOTS}`);
  await browser.close();
  process.exit(failed.length === 0 ? 0 : 1);
} catch (err) {
  console.error("煙霧中止：", err);
  if (browser) await browser.close();
  process.exit(1);
}
