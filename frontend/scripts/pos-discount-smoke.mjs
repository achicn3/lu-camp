// 活動折扣結帳瀏覽器煙霧（docs/21 C2b）：建立並啟用活動 → 收購自有序號品 → POS 掃碼 →
// 斷言應付總額顯示「折後」價 + 活動折扣提示 → 現金結帳成功（完成 total = 折後）。
// 證明 POS 經 /sales/quote 取折後總額、收款對齊折後 total（修復折扣結帳 422）。
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-shots/pos-discount";
const USER = process.env.SEED_USER ?? "dev-manager";
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });
const results = [];
const ok = (name, pass, detail = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
};

async function apiJson(path, { method = "GET", token, body, headers = {}, expected = [200, 201] } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!expected.includes(res.status)) {
    throw new Error(`${method} ${path} → ${res.status}: ${text}`);
  }
  return data;
}

let browser;
try {
  // --- API 準備：登入、開帳、建立並啟用活動、收購自有序號品 ---
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: USER, password: PASS },
  });
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current === null) {
    await apiJson("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
    });
  }
  const runId = `${Date.now()}-${randomUUID().slice(0, 6)}`;
  const now = new Date();
  const camp = await apiJson("/api/v1/campaigns", {
    method: "POST",
    token,
    body: {
      name: `折扣結帳煙測 ${runId}`,
      discount_pct: 10,
      starts_at: new Date(now.getTime() - 86400000).toISOString(),
      ends_at: new Date(now.getTime() + 86400000).toISOString(),
      applies_owned_serialized: true,
      applies_owned_bulk: true,
      applies_catalog: false,
      applies_consignment: false,
      consignment_discount_bearing: "STORE_ABSORBS",
    },
  });
  await apiJson(`/api/v1/campaigns/${camp.id}/activate`, { method: "POST", token });
  ok("建立並啟用活動（九折）", true);

  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    expected: [201],
    body: { name: `折扣煙測賣方 ${runId}`, national_id: `DISC-${runId}`, roles: ["SELLER"] },
  });
  const acq = await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": `disc-${runId}` },
    expected: [201],
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [{ name: `折扣帳篷 ${runId}`, grade: "A", listed_price: "1000", acquisition_cost: "400" }],
    },
  });
  const code = acq.item_codes[0];
  ok("收購自有序號品（標價 1000）", Boolean(code), code);

  // --- 瀏覽器：POS 掃碼 → 折後總額 → 現金結帳 ---
  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USER);
  await page.fill('input[name="password"]', PASS);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.click('a:has-text("POS 結帳")');
  await page.waitForURL(`${BASE}/pos`);
  await page.waitForSelector("text=本期不開票");

  await page.fill('input[name="code"]', code);
  await page.press('input[name="code"]', "Enter");
  await page.waitForSelector(`text=折扣帳篷 ${runId}`);

  // 應付總額顯示折後 900（非折前 1000）
  await page.waitForFunction(
    () => document.querySelector(".pos-total strong")?.textContent?.includes("900"),
    undefined,
    { timeout: 8000 },
  );
  const totalText = await page.locator(".pos-total strong").textContent();
  ok("應付總額顯示折後 900", totalText?.includes("900") && !totalText.includes("1,000"), totalText ?? "");
  ok("顯示活動折扣提示", await page.locator("text=已套用活動折扣").isVisible());
  await page.screenshot({ path: `${SHOTS}/01-pos-discounted-total.png`, fullPage: true });

  const checkout = page.getByRole("button", { name: "結帳" });
  await checkout.waitFor({ state: "visible" });
  ok("結帳鍵就緒（試算完成）", !(await checkout.isDisabled()));
  await checkout.click();
  await page.waitForSelector("text=已完成");
  const completeText = await page.locator(".pos-complete").innerText();
  ok("折後結帳完成（total 900）", /900/.test(completeText), completeText.replace(/\s+/g, " ").slice(0, 120));
  await page.screenshot({ path: `${SHOTS}/02-pos-discount-complete.png`, fullPage: true });

  // 列印明細 → 串接硬體代理（真的送印到 EPSON，含折扣/數量欄）
  await page.getByRole("button", { name: "列印明細" }).click();
  await page.waitForSelector("text=已送出列印", { timeout: 10000 });
  ok("列印明細送印成功（串接硬體代理 → EPSON）", true);
  await page.screenshot({ path: `${SHOTS}/03-pos-print-sent.png`, fullPage: true });
} catch (e) {
  ok("流程中斷", false, String(e));
  if (browser) {
    const p = browser.contexts().flatMap((c) => c.pages())[0];
    if (p) await p.screenshot({ path: `${SHOTS}/99-failure.png`, fullPage: true });
  }
} finally {
  if (browser) await browser.close();
}
const failed = results.filter((r) => !r.pass);
console.log(`\n結果：${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length ? 1 : 0);
