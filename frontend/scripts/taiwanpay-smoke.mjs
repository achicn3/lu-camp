// 台灣Pay（docs/30 P1 前端）瀏覽器煙霧測試：
// 設定頁「行動支付設定」卡（LINE Pay 開關＋手續費率）→ POS 以台灣Pay 收款（非現金、不需開帳、
// 顯示店家負擔手續費）→ 結帳完成收款方式顯示「台灣Pay」。
// 執行：node scripts/taiwanpay-smoke.mjs
// 需 backend:8000 + frontend:3000 已起、dev-manager 可登入。
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";
import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = stripTrailingSlash(process.env.SMOKE_BASE ?? "http://localhost:3000");
const API_BASE = stripTrailingSlash(process.env.SMOKE_API_BASE ?? "http://localhost:8000");
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "codex-test", "taiwanpay-smoke");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

function stripTrailingSlash(v) {
  return v.replace(/\/+$/, "");
}
function idem(label) {
  return `tpay-${label}-${randomUUID()}`.slice(0, 80);
}
function money(amount) {
  return Number(amount).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

async function apiJson(path, { method = "GET", token = null, body, headers = {}, expected = [200] } = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(`${method} ${path} non-JSON (${response.status}): ${text}`);
    }
  }
  if (!expected.includes(response.status)) {
    const detail = data && typeof data === "object" && "detail" in data ? JSON.stringify(data.detail) : text;
    throw new Error(`${method} ${path} expected ${expected.join("/")} got ${response.status}: ${detail}`);
  }
  return data;
}

async function loginApi() {
  const data = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: USERNAME, password: PASSWORD },
  });
  if (!data?.access_token) throw new Error("登入 API 未回傳 access_token");
  return data.access_token;
}

async function createSerializedFixture(token, sellerId, { name, price, cost }) {
  const result = await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": idem("serialized") },
    expected: [201],
    body: {
      type: "BUYOUT",
      contact_id: sellerId,
      payout_method: "CASH",
      note: "台灣Pay smoke fixture",
      items: [{ name, grade: "A", listed_price: String(price), acquisition_cost: String(cost) }],
    },
  });
  const code = result.item_codes?.[0];
  if (!code) throw new Error("收購序號品未回傳 item_code");
  return { code, name, price };
}

async function prepareFixtures() {
  const token = await loginApi();
  const runId = `${Date.now()}-${randomUUID().slice(0, 6)}`;
  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    expected: [201],
    body: {
      name: `台灣Pay 煙霧賣方 ${runId}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
      member_points: 0,
      source_note: "taiwanpay smoke",
    },
  });
  const item = await createSerializedFixture(token, seller.id, {
    name: `台灣Pay 帳篷 ${runId}`,
    price: 1500,
    cost: 600,
  });
  // 設手續費率：台灣Pay 2%、LINE Pay 1.5% 且啟用（驗設定頁欄位＋POS 手續費顯示）。
  await apiJson("/api/v1/settings", {
    method: "PATCH",
    token,
    body: { taiwanpay_fee_pct: "0.0200", linepay_fee_pct: "0.0150", linepay_enabled: true },
  });
  // 折後總額以 quote 為準（demo seed 有生效活動「開幕九折」，售價 ×0.9）。手續費＝折後 ×2%。
  const quote = await apiJson("/api/v1/sales/quote", {
    method: "POST",
    token,
    body: { lines: [{ line_type: "SERIALIZED", item_code: item.code, qty: 1 }] },
  });
  const total = Number.parseInt(String(quote.total).replace(/,/g, ""), 10);
  const fee = Math.round(total * 0.02);
  ok("API 測試資料＋費率設定完成", true, `item=${item.code} 折後=${total} 手續費=${fee}`);
  return { item, total, fee };
}

let browser;
try {
  const { item, total, fee } = await prepareFixtures();

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

  // 登入
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  // 1) 設定頁：行動支付設定卡渲染＋值正確
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  await page.waitForSelector('h2:has-text("行動支付設定")');
  const linepayToggle = page.locator('input[name="linepay_enabled"]');
  const linepayFee = page.locator('input[name="linepay_fee_pct"]');
  const taiwanpayFee = page.locator('input[name="taiwanpay_fee_pct"]');
  ok("行動支付設定卡出現", true);
  ok("LINE Pay 開關反映啟用", await linepayToggle.isChecked());
  ok("LINE Pay 費率顯示 1.5", (await linepayFee.inputValue()) === "1.5", await linepayFee.inputValue());
  ok("台灣Pay 費率顯示 2", (await taiwanpayFee.inputValue()) === "2", await taiwanpayFee.inputValue());
  await page.screenshot({ path: `${SHOTS}/01-settings-mobile-payment.png` });

  // 2) POS：台灣Pay 收款（非現金，不需開帳）
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector('input[name="code"]');
  await page.fill('input[name="code"]', item.code);
  await page.press('input[name="code"]', "Enter");
  await page.waitForSelector(`text=${item.name}`);
  ok("掃描序號品加入購物車", true);

  await page.locator(".pos-tender-mode", { hasText: "台灣Pay" }).click();
  // 手續費提示（店家負擔 = 折後總額 × 2%）
  await page.waitForSelector("text=本筆手續費");
  const feeHint = await page.locator(".pos-tender .hint", { hasText: "台灣Pay 收款" }).textContent();
  ok(
    `台灣Pay 手續費提示顯示（店家負擔 ${fee}）`,
    (feeHint?.includes(money(fee)) && feeHint?.includes("店家負擔")) ?? false,
    feeHint ?? "",
  );
  await page.screenshot({ path: `${SHOTS}/02-pos-taiwanpay-tender.png` });

  // 結帳按鈕未被開帳擋（非現金不需開帳）
  const checkoutBtn = page.locator('button:has-text("結帳")');
  ok("台灣Pay 不需開帳即可結帳（結帳鍵啟用）", !(await checkoutBtn.isDisabled()));
  await checkoutBtn.click();
  await page.waitForSelector("text=已完成");
  const methodDd = page.locator(".pos-complete .stat-list dd").filter({ hasText: /^台灣Pay$/ });
  ok("結帳完成收款方式＝台灣Pay", await methodDd.isVisible());
  const totalDd = await page.locator(".pos-complete .stat-list dd").first().textContent();
  ok(`完成總額 = ${money(total)}`, totalDd?.includes(money(total)) ?? false, totalDd ?? "");
  await page.screenshot({ path: `${SHOTS}/03-pos-taiwanpay-complete.png` });

  // 3) 後端驗證：最近一筆 sale 明細 payment_method=TAIWAN_PAY、tender fee_amount 正確、非現金
  const token = await loginApi();
  const sales = await apiJson("/api/v1/sales?limit=5", { token });
  const latest = (Array.isArray(sales) ? sales : (sales.items ?? []))[0];
  const detail = latest ? await apiJson(`/api/v1/sales/${latest.id}`, { token }) : null;
  ok(
    "API：最近一筆 payment_method=TAIWAN_PAY",
    detail?.payment_method === "TAIWAN_PAY",
    detail ? `#${detail.id} ${detail.payment_method}` : "查無",
  );
  if (detail) {
    const tp = (detail.tenders ?? []).find((t) => t.tender_type === "TAIWAN_PAY");
    ok(
      `API：TAIWAN_PAY tender amount=${total}、fee_amount=${fee}`,
      tp?.amount === String(total) && tp?.fee_amount === String(fee),
      tp ? `amount=${tp.amount} fee=${tp.fee_amount}` : "無 tender",
    );
  }

  const failed = results.filter((r) => !r.pass);
  console.log(`\n${failed.length === 0 ? "✅ 全數通過" : `❌ ${failed.length} 項失敗`} (${results.length} 檢查)`);
  console.log(`截圖：${SHOTS}`);
  await browser.close();
  process.exit(failed.length === 0 ? 0 : 1);
} catch (err) {
  console.error("煙霧測試中止：", err);
  if (browser) await browser.close();
  process.exit(1);
}
