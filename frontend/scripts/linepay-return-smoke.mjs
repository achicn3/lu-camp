// LINE Pay 部分退款 UI（docs/30 P4b）瀏覽器煙霧：真收一筆 2 行 LINE Pay → /sales 退貨對話框
// （LINE Pay 提示、可選品項）→ 退其中一行（真部分退款）→ DB refunded 部分、狀態 COMPLETE →
// 收尾退剩餘行（全退 REFUNDED）。
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
const SHOTS = join(homedir(), "tmp", "codex-test", "linepay-return-smoke");
mkdirSync(SHOTS, { recursive: true });
const results = [];
const ok = (n, p, d = "") => {
  results.push({ p });
  console.log(`${p ? "✅" : "❌"} ${n}${d ? `：${d}` : ""}`);
};
const { execSync } = await import("node:child_process");
const psql = (sql) =>
  execSync(`docker exec lu-camp-db-1 psql -U lucamp -d lucamp_e2e -tAc "${sql}"`, {
    encoding: "utf8",
  }).trim();

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
      name: `LP退貨賣方${runId}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
      member_points: 0,
      source_note: "linepay return smoke",
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
      note: "LP退貨",
      items: [
        { name: `LP退A${runId}`, grade: "A", listed_price: "600", acquisition_cost: "200" },
        { name: `LP退B${runId}`, grade: "A", listed_price: "400", acquisition_cost: "100" },
      ],
    },
  });
  const [codeA, codeB] = acq.item_codes;
  const lines = [
    { line_type: "SERIALIZED", item_code: codeA, qty: 1 },
    { line_type: "SERIALIZED", item_code: codeB, qty: 1 },
  ];

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));
  const oneTimeKey = await decodeKey(page);

  const quote = await api("/api/v1/sales/quote", { method: "POST", token, body: { lines } });
  const total = String(quote.total);
  const sale = await api("/api/v1/sales", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `lp-return-${runId}` },
    body: {
      lines,
      tenders: [{ tender_type: "LINE_PAY", amount: total, line_pay_one_time_key: oneTimeKey }],
    },
  });
  ok("真收一筆 2 行 LINE Pay", sale.payment_method === "LINE_PAY", `#${sale.id} 折後 ${total}`);

  // 登入 → /sales → 該單退貨
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.click(`button[aria-label="退貨銷售 ${sale.id}"]`);

  // 對話框：LINE Pay 提示（非「不支援」擋阻）
  await page.waitForSelector('[role="dialog"][aria-label="退貨"]');
  ok(
    "退貨對話框顯示 LINE Pay 退款提示（未被擋）",
    await page.locator("text=LINE Pay 退款自動退回客人").isVisible(),
  );
  // 退第一行（qty=1）
  const firstQty = page.locator('[role="dialog"] input[type="number"]').first();
  await firstQty.fill("1");
  await page.fill('[role="dialog"] input[name="reason"], [role="dialog"] textarea', "客人只退一件").catch(
    async () => {
      // reason 欄位可能無 name：退回填第一個文字輸入
      await page.locator('[role="dialog"] input[type="text"]').first().fill("客人只退一件");
    },
  );
  await page.screenshot({ path: `${SHOTS}/01-return-dialog.png` });
  await page.click('[role="dialog"] button:has-text("確認退貨")');
  await page.waitForSelector('[role="dialog"][aria-label="退貨"]', { state: "detached", timeout: 15000 });
  ok("部分退貨送出成功（對話框關閉）", true);

  // DB：部分退款（refunded < total、狀態 COMPLETE）
  const row = psql(
    `SELECT status || '|' || refunded_amount || '|' || amount FROM linepay_transactions WHERE sale_id=${sale.id}`,
  );
  const [st, refunded, amount] = row.split("|");
  ok(
    "DB 部分退款：refunded < amount、狀態 COMPLETE",
    st === "COMPLETE" && Number(refunded) > 0 && Number(refunded) < Number(amount),
    row,
  );

  // 收尾：退剩餘行（全退 → REFUNDED）
  const detail = await api(`/api/v1/sales/${sale.id}`, { token });
  const remaining = (detail.lines || [])
    .filter((l) => (l.returned_qty ?? 0) < l.qty)
    .map((l) => ({ sale_line_id: l.id, qty: l.qty - (l.returned_qty ?? 0) }));
  if (remaining.length > 0) {
    await api("/api/v1/returns", {
      method: "POST",
      token,
      expect: [201],
      headers: { "Idempotency-Key": `lp-return-rest-${runId}` },
      body: { sale_id: sale.id, reason: "收尾全退", lines: remaining },
    });
  }
  const finalRow = psql(
    `SELECT status || '|' || refunded_amount FROM linepay_transactions WHERE sale_id=${sale.id}`,
  );
  ok("收尾全退 → REFUNDED 全額", finalRow === `REFUNDED|${amount}`, finalRow);

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
