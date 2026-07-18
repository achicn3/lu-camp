// LINE Pay 手續費進毛利報表（docs/30 P4a）瀏覽器煙霧：真收一筆 LINE Pay → 銷售毛利報表顯示
// 淨毛利/支付手續費合計/收款方式分列（含 LINE Pay 列）→ 收尾作廢真退款。
// 執行：node scripts/linepay-report-smoke.mjs（需 backend:8000 帶 LINEPAY_* env、frontend:3000）。
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
const SHOTS = join(homedir(), "tmp", "codex-test", "linepay-report-smoke");
mkdirSync(SHOTS, { recursive: true });
const results = [];
const ok = (n, p, d = "") => {
  results.push({ p });
  console.log(`${p ? "✅" : "❌"} ${n}${d ? `：${d}` : ""}`);
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
      name: `LP報表賣方${runId}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
      member_points: 0,
      source_note: "linepay report smoke",
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
      note: "LP報表",
      items: [{ name: `LP報表帳篷${runId}`, grade: "A", listed_price: "800", acquisition_cost: "300" }],
    },
  });
  const code = acq.item_codes[0];

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));
  const oneTimeKey = await decodeKey(page);

  // 折後總額（demo seed 有生效活動「開幕九折」）
  const quote = await api("/api/v1/sales/quote", {
    method: "POST",
    token,
    body: { lines: [{ line_type: "SERIALIZED", item_code: code, qty: 1 }] },
  });
  const total = String(quote.total);

  // 真收一筆 LINE Pay（保留、供報表統計）
  const sale = await api("/api/v1/sales", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `lp-report-${runId}` },
    body: {
      lines: [{ line_type: "SERIALIZED", item_code: code, qty: 1 }],
      tenders: [{ tender_type: "LINE_PAY", amount: total, line_pay_one_time_key: oneTimeKey }],
    },
  });
  ok("真收一筆 LINE Pay（供報表統計）", sale.payment_method === "LINE_PAY", `#${sale.id} 折後 ${total}`);

  // 登入 → 報表 → 銷售毛利分頁
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
  await page.click('button:has-text("銷售毛利")');
  await page.waitForSelector("text=淨毛利（扣支付手續費）", { timeout: 15000 });
  ok("毛利報表出現「淨毛利（扣支付手續費）」", true);
  ok(
    "出現「支付手續費合計（店家成本）」",
    await page.locator("td", { hasText: "支付手續費合計（店家成本）" }).isVisible(),
  );
  const breakdown = page.locator(".inv-table-wrap", { hasText: "收款方式分列" });
  await breakdown.waitFor({ state: "visible", timeout: 10000 });
  const lineRow = breakdown.locator("tr", { hasText: "LINE Pay" });
  ok("收款方式分列含 LINE Pay 列", await lineRow.first().isVisible());
  // 該列有三欄（方式/收款額/手續費）：手續費欄非空即證分列到位（本次 720×1.5%≈11）
  const cells = await lineRow.first().locator("td").allTextContents();
  ok("LINE Pay 列含收款額＋手續費三欄", cells.length === 3, cells.join(" | "));
  await page.screenshot({ path: `${SHOTS}/01-margin-payment-fee.png`, fullPage: true });

  // 收尾：作廢真退款
  await api(`/api/v1/sales/${sale.id}/void`, { method: "POST", token });
  ok("收尾作廢真退款", true, `void #${sale.id}`);

  const failed = results.filter((r) => !r.p);
  console.log(`\n${failed.length === 0 ? "✅ 全數通過" : `❌ ${failed.length} 失敗`}（${results.length} 檢查）`);
  console.log(`截圖：${SHOTS}`);
  await browser.close();
  process.exit(failed.length === 0 ? 0 : 1);
} catch (err) {
  console.error("煙霧中止：", err);
  if (browser) await browser.close();
  process.exit(1);
}
