// 贈品與臨時折扣的瀏覽器煙霧測試：加入商品 → 單品折扣 → 整單折扣 → 改為贈品 →
// 現金結帳 → 驗庫存有扣、金額摘要正確。
// 執行：node scripts/gift-discount-smoke.mjs
// 需 backend:8000 + frontend:3000 已起、dev-manager 可登入。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = stripTrailingSlash(process.env.SMOKE_BASE ?? "http://localhost:3000");
const API_BASE = stripTrailingSlash(process.env.SMOKE_API_BASE ?? "http://localhost:8000");
const SHOTS =
  process.env.SMOKE_SHOTS ??
  join(homedir(), "tmp", "codex-test", "gift-discount-smoke");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

function stripTrailingSlash(value) {
  return value.replace(/\/+$/, "");
}

function money(amount) {
  return Number(amount).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

async function apiJson(
  path,
  { method = "GET", token = null, body = undefined, expected = [200] } = {},
) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!expected.includes(response.status)) {
    throw new Error(
      `${method} ${path} expected ${expected.join("/")} got ${response.status}: ${text}`,
    );
  }
  return data;
}

async function login() {
  const data = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: USERNAME, password: PASSWORD },
  });
  if (!data?.access_token) throw new Error("登入 API 未回傳 access_token");
  return data.access_token;
}

async function ensureOpenCashSession(token) {
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current !== null) return current;
  return await apiJson("/api/v1/cash-sessions/open", {
    method: "POST",
    token,
    body: { opening_float: "2000" },
    expected: [201],
  });
}

/** 找兩個有庫存的一般商品；煙霧測試不建資料，用既有 seed。 */
async function pickProducts(token) {
  const page1 = await apiJson("/api/v1/catalog-products?limit=100", { token });
  const items = (page1.items ?? page1).filter(
    (p) => p.quantity_on_hand >= 3 && Number(p.unit_price) > 0,
  );
  if (items.length < 2) {
    throw new Error("需要至少兩個有庫存的一般商品，請先 seed");
  }
  return [items[0], items[1]];
}

async function loginBrowser(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
}

/** 掃一個 SKU 並等它真的進購物車：掃碼輸入在解析期間會被停用，連續 fill 會撞上。 */
async function addBySku(page, sku, expectedRows) {
  const input = page.locator('input[name="code"]');
  await input.waitFor({ state: "visible" });
  await page.waitForFunction(
    () => document.querySelector('input[name="code"]')?.disabled === false,
    undefined,
    { timeout: 15_000 },
  );
  await input.fill(sku);
  await input.press("Enter");
  try {
    await page.waitForSelector(`.pos-cart tbody tr:nth-child(${expectedRows})`, {
      timeout: 15_000,
    });
  } catch (error) {
    const alertText = await page
      .locator('[role="alert"]')
      .first()
      .textContent()
      .catch(() => null);
    throw new Error(`掃碼 ${sku} 未進購物車${alertText ? `：${alertText}` : ""}`, {
      cause: error,
    });
  }
}

async function expectTotal(page, amount, label) {
  await page.waitForFunction(
    (expected) =>
      document.querySelector(".pos-total strong")?.textContent?.includes(expected),
    money(amount),
    { timeout: 10_000 },
  );
  ok(label, true, `應付 ${money(amount)}`);
}

/** 讀畫面上的應付總額。門市可能有生效活動，基準額不自算、一律以畫面為準。 */
async function readTotal(page) {
  const text = await page.locator(".pos-total strong").textContent();
  return Number.parseInt(String(text).replace(/[^\d]/g, ""), 10);
}

let browser;
try {
  const token = await login();
  await ensureOpenCashSession(token);
  const [productA, productB] = await pickProducts(token);
  const beforeB = productB.quantity_on_hand;
  ok("測試資料就緒", true, `${productA.name} / ${productB.name}`);

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

  await loginBrowser(page);
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });

  // 1) 加入兩樣商品
  await addBySku(page, productA.sku, 1);
  await addBySku(page, productB.sku, 2);
  await page.waitForTimeout(800); // 等後端試算回來（活動折扣可能生效）
  const listed = await readTotal(page);
  ok("兩樣商品加入購物車", listed > 0, `試算應付 ${money(listed)}`);
  await page.screenshot({ path: `${SHOTS}/01-cart.png`, fullPage: true });

  // 2) 單品折扣 −50
  await page.locator(`button[aria-label="折扣 ${productA.name}"]`).click();
  await page.waitForSelector('[role="dialog"][aria-label="新增折扣"]');
  await page.fill('input[aria-label="折扣數值"]', "50");
  await page.screenshot({ path: `${SHOTS}/02-item-discount-dialog.png` });
  await page.click('button:has-text("套用折扣")');
  await expectTotal(page, listed - 50, "單品折扣已套用");
  ok(
    "折扣清單顯示這筆單品折扣",
    await page
      .locator(".pos-discount-list li", { hasText: productA.name })
      .isVisible(),
  );
  await page.screenshot({ path: `${SHOTS}/03-item-discount.png`, fullPage: true });

  // 3) 整單折扣 10%
  await page.click('button:has-text("整單折扣")');
  await page.waitForSelector('[role="dialog"][aria-label="新增折扣"]');
  await page.locator('input[type="radio"]').nth(1).check();
  await page.fill('input[aria-label="折扣數值"]', "10");
  await page.click('button:has-text("套用折扣")');
  const afterOrder = listed - 50 - Math.round((listed - 50) * 0.1);
  await expectTotal(page, afterOrder, "整單折扣以折後餘額為基礎分攤");
  await page.screenshot({ path: `${SHOTS}/04-order-discount.png`, fullPage: true });

  // 4) 把第二樣改為贈品
  await page.locator(`button[aria-label="改為贈品 ${productB.name}"]`).click();
  await page.waitForSelector('[role="dialog"][aria-label="改為贈品"]');
  await page.selectOption('[role="dialog"] select', { index: 1 });
  await page.screenshot({ path: `${SHOTS}/05-gift-dialog.png` });
  await page.click('button:has-text("確認贈送")');
  await page.waitForSelector(".pos-gift-badge");
  // 贈品價值來自後端試算，要等重算回來才會出現（badge 是本機狀態，先一步顯示）。
  const giftSummary = page.locator(".pos-summary", { hasText: "贈品價值" });
  await giftSummary.waitFor({ state: "visible", timeout: 15_000 });
  ok("贈品價值單獨顯示、不計入應付", true, await giftSummary.innerText());
  await page.screenshot({ path: `${SHOTS}/06-gift-applied.png`, fullPage: true });

  // 5) 現金結帳
  const totalText = await page.locator(".pos-total strong").textContent();
  await page.locator(".pos-tender-mode", { hasText: "現金" }).click();
  await page.click('button:has-text("結帳")');
  await page.waitForSelector(".pos-complete", { timeout: 20_000 });
  ok("結帳完成", true, `應付 ${totalText}`);
  await page.screenshot({ path: `${SHOTS}/07-completed.png`, fullPage: true });

  // 6) 贈品確實出庫
  const after = await apiJson(
    `/api/v1/catalog-products/${productB.id}/detail`,
    { token },
  );
  ok(
    "贈品照樣扣庫存",
    after.quantity_on_hand === beforeB - 1,
    `${beforeB} → ${after.quantity_on_hand}`,
  );

  // 7) 退貨：只退主商品時，畫面必須擋下並要求說明贈品為何不收回
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.locator("table tbody tr").first().locator('button:has-text("退貨")').click();
  const dialog = page.locator('[role="dialog"]');
  await dialog.waitFor({ state: "visible" });
  // 只退主商品（第一列），贈品不退
  await dialog.locator(".return-qty-input").first().fill("1");
  await dialog.locator('input[type="text"]').first().fill("尺寸不合");
  const giftNotice = dialog.locator(".return-gift-notice");
  await giftNotice.waitFor({ state: "visible", timeout: 15_000 });
  ok("退主商品未退贈品：畫面提示並要求說明", true, await giftNotice.innerText());
  const submitButton = dialog.locator('button:has-text("確認退貨")');
  ok("未填說明時不得送出", await submitButton.isDisabled());
  await page.screenshot({ path: `${SHOTS}/08-return-gift-notice.png`, fullPage: true });

  // 8) 填了說明就能送出，且退款只退實付（不是牌價）
  await dialog.locator('input[aria-label="贈品不收回的原因"]').fill("已拆封無法回售");
  await submitButton.waitFor({ state: "visible" });
  ok("填了說明後可送出", !(await submitButton.isDisabled()));
  const refundLabel = await submitButton.textContent();
  await submitButton.click();
  await page.waitForSelector("text=退貨完成", { timeout: 20_000 });
  ok("退貨完成（退款依實付）", true, String(refundLabel).trim());
  await page.screenshot({ path: `${SHOTS}/09-return-done.png`, fullPage: true });
  // 9) 報表：折扣與贈品各自看得到數字
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
  await page.locator('button:has-text("臨時折扣")').click();
  await page.waitForSelector("text=折扣總額", { timeout: 15_000 });
  ok(
    "折扣報表有數字",
    await page.locator(".rpt-summary").first().isVisible(),
    (await page.locator(".rpt-summary").first().innerText()).replace(/\n/g, " "),
  );
  await page.screenshot({ path: `${SHOTS}/10-report-discounts.png`, fullPage: true });

  await page.locator('button:has-text("贈品")').first().click();
  await page.waitForSelector("text=原價價值", { timeout: 15_000 });
  ok(
    "贈品報表有數字",
    await page.locator(".rpt-summary").first().isVisible(),
    (await page.locator(".rpt-summary").first().innerText()).replace(/\n/g, " "),
  );
  await page.screenshot({ path: `${SHOTS}/11-report-gifts.png`, fullPage: true });

  // 10) 設定頁可管理原因代碼（停用不實刪）
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  await page.waitForSelector("text=贈品原因代碼", { timeout: 15_000 });
  const giftReasonCard = page.locator(".card", { hasText: "贈品原因代碼" }).first();
  ok("設定頁列出贈品原因代碼", await giftReasonCard.isVisible());
  await page.screenshot({ path: `${SHOTS}/12-settings-reasons.png`, fullPage: true });
} catch (error) {
  ok("煙霧測試", false, String(error));
} finally {
  if (browser) await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
