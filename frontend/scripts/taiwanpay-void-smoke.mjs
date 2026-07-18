// 台灣Pay 作廢須手動退款確認（docs/30 finding #3）瀏覽器＋API 煙霧：
// API：TAIWAN_PAY 單 void 無 ack→409、帶 ack→200。UI：/sales 作廢對話框顯示手動退款勾選、
// 未勾停用確認、勾選後可作廢。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = "http://localhost:3000";
const API = "http://localhost:8000";
const SHOTS = join(homedir(), "tmp", "codex-test", "taiwanpay-void-smoke");
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
  return { status: res.status, data: text ? JSON.parse(text) : null };
}

let browser;
try {
  const { data: login } = await api("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
    expect: [200],
  });
  const token = login.access_token;
  const cur = await api("/api/v1/cash-sessions/current", { token });
  if (cur.data === null)
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
      name: `TW作廢賣方${runId}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
      member_points: 0,
      source_note: "tw void smoke",
    },
  });
  async function makeTaiwanPaySale() {
    const acq = await api("/api/v1/acquisitions", {
      method: "POST",
      token,
      expect: [201],
      headers: { "Idempotency-Key": `acq-${runId}-${Math.random()}` },
      body: {
        type: "BUYOUT",
        contact_id: seller.data.id,
        payout_method: "CASH",
        note: "tw",
        items: [
          { name: `TW作廢品${runId}${Math.random()}`, grade: "A", listed_price: "1000", acquisition_cost: "300" },
        ],
      },
    });
    const code = acq.data.item_codes[0];
    const quote = await api("/api/v1/sales/quote", {
      method: "POST",
      token,
      body: { lines: [{ line_type: "SERIALIZED", item_code: code, qty: 1 }] },
    });
    const sale = await api("/api/v1/sales", {
      method: "POST",
      token,
      expect: [201],
      headers: { "Idempotency-Key": `tw-${runId}-${Math.random()}` },
      body: {
        lines: [{ line_type: "SERIALIZED", item_code: code, qty: 1 }],
        tenders: [{ tender_type: "TAIWAN_PAY", amount: String(quote.data.total) }],
      },
    });
    return sale.data;
  }

  // ── API 驗證：void 無 ack → 409、帶 ack → 200 ──
  const apiSale = await makeTaiwanPaySale();
  const noAck = await api(`/api/v1/sales/${apiSale.id}/void`, {
    method: "POST",
    token,
    expect: [409],
  });
  ok("API：台灣Pay 作廢無手動退款確認 → 409", noAck.status === 409, noAck.data?.detail?.slice(0, 40));
  const withAck = await api(`/api/v1/sales/${apiSale.id}/void?manual_refund_ack=true`, {
    method: "POST",
    token,
    expect: [200],
  });
  ok("API：帶手動退款確認 → 200 作廢成功", withAck.status === 200);

  // ── UI 驗證：作廢對話框手動退款勾選閘門 ──
  const uiSale = await makeTaiwanPaySale();
  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.click(`button[aria-label="作廢銷售 ${uiSale.id}"]`);
  await page.waitForSelector('[role="dialog"][aria-label="作廢銷售確認"]');
  ok(
    "作廢對話框顯示台灣Pay 手動退款勾選",
    await page.locator("text=我已於台灣Pay App 完成退款給客人").isVisible(),
  );
  const confirmBtn = page.locator('[role="dialog"] button:has-text("確認作廢")');
  ok("未勾選時確認作廢停用", await confirmBtn.isDisabled());
  await page.check('input[name="manual_refund_ack"]');
  ok("勾選後確認作廢啟用", !(await confirmBtn.isDisabled()));
  await page.screenshot({ path: `${SHOTS}/01-taiwanpay-void-ack.png` });
  await confirmBtn.click();
  await page.waitForSelector('[role="dialog"][aria-label="作廢銷售確認"]', {
    state: "detached",
    timeout: 15000,
  });
  ok("勾選後作廢送出成功", true);

  const failed = results.filter((r) => !r.p);
  console.log(`\n${failed.length === 0 ? "✅ 全數通過" : `❌ ${failed.length} 失敗`}（${results.length} 檢查）`);
  await browser.close();
  process.exit(failed.length === 0 ? 0 : 1);
} catch (err) {
  console.error("煙霧中止：", err);
  if (browser) await browser.close();
  process.exit(1);
}
