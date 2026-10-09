// 餐飲可用購物金（ADR-031，店主 2026-10-09）瀏覽器 E2E：
// 1) 純餐點（拿鐵 2 杯 × $150＝$300）在真 POS 選外帶、會員、「購物金＋其他付款」購物金 $100＋現金 $200，
//    畫面不再出現「餐飲不可用購物金」；客人在真客顯簽名後結帳成立。
// 2) /fnb-sales 退 1 杯：預估與實際退款都是購物金 $100＋現金 $50（購物金先算在餐點那份）。
// 3) API 核對：會員購物金回到 $500。
//
// 執行：node scripts/store-credit-food-smoke.mjs
// 需 backend、frontend、dev-manager / dev-kiosk 測試帳號（SMOKE_BASE／SMOKE_API_BASE 可改位址）。
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const KIOSK_USERNAME = process.env.SMOKE_KIOSK_USERNAME ?? "dev-kiosk";
const KIOSK_PASSWORD = process.env.SMOKE_KIOSK_PASSWORD ?? "dev-test-123456";
const SHOTS =
  process.env.SMOKE_SHOTS ?? resolve(homedir(), "tmp", "lu-camp-shots", "store-credit-food");

mkdirSync(SHOTS, { recursive: true });

const checks = [];
function ok(name, pass, detail = "") {
  checks.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(
  path,
  { method = "GET", token = null, body = undefined, headers = {}, expected = null } = {},
) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  const accepted = expected ?? [200, 201];
  if (!accepted.includes(response.status)) {
    throw new Error(`${method} ${path} → ${response.status}: ${text.slice(0, 500)}`);
  }
  return data;
}

function tenderMap(tenders) {
  return Object.fromEntries(tenders.map((tender) => [tender.tender_type, Number(tender.amount)]));
}

async function prepareFixtures(token) {
  const originalSettings = await apiJson("/api/v1/settings", { token });
  await apiJson("/api/v1/settings", {
    method: "PATCH",
    token,
    body: { einvoice_enabled: false, store_credit_min_spend: "0" },
  });
  if ((await apiJson("/api/v1/cash-sessions/current", { token })) === null) {
    await apiJson("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
    });
  }
  const stamp = `${Date.now()}-${randomUUID().slice(0, 6)}`;
  const member = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body: {
      name: `餐飲購物金會員 ${stamp}`,
      phone: `09${Date.now().toString().slice(-8)}`,
      roles: ["MEMBER"],
      source_note: "store credit food browser E2E",
    },
  });
  await apiJson(`/api/v1/contacts/${member.id}/store-credit/adjustments`, {
    method: "POST",
    token,
    headers: { "Idempotency-Key": `sc-food-credit-${stamp}` },
    body: { amount: "500", reason: "餐飲購物金瀏覽器 E2E 備測" },
  });
  const latte = await apiJson("/api/v1/menu-items", {
    method: "POST",
    token,
    body: { name: `購物金拿鐵-${stamp}`, unit_price: "150" },
  });
  return { originalSettings, member, latte };
}

async function pairKiosk(browser, managerToken, installationId) {
  const kioskContext = await browser.newContext({ viewport: { width: 834, height: 1112 } });
  const kioskPage = await kioskContext.newPage();
  await kioskPage.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await kioskPage.fill('input[name="username"]', KIOSK_USERNAME);
  await kioskPage.fill('input[name="password"]', KIOSK_PASSWORD);
  await kioskPage.click('button:has-text("啟用裝置")');
  await kioskPage.waitForSelector(".kiosk-pairing-code", { timeout: 8_000 });
  const pairingCode = (await kioskPage.textContent(".kiosk-pairing-code"))?.trim();
  if (!pairingCode) throw new Error("客顯未產生配對碼");
  const terminal = await apiJson("/api/v1/customer-display/terminals", {
    method: "POST",
    token: managerToken,
    body: { installation_id: installationId, name: `餐飲購物金 E2E 櫃檯 ${Date.now()}` },
  });
  await apiJson(`/api/v1/customer-display/terminals/${terminal.id}/pair`, {
    method: "POST",
    token: managerToken,
    body: { pairing_code: pairingCode },
  });
  await kioskPage.waitForSelector('h1:has-text("露坑選物露營用品")', { timeout: 8_000 });
  return { kioskContext, kioskPage };
}

async function drawSignature(page) {
  const canvas = page.locator("canvas.kiosk-sign-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("找不到購物金簽名畫布");
  const points = [
    [0.15, 0.55],
    [0.3, 0.25],
    [0.45, 0.7],
    [0.6, 0.3],
    [0.75, 0.62],
    [0.85, 0.4],
  ];
  await page.mouse.move(box.x + box.width * points[0][0], box.y + box.height * points[0][1]);
  await page.mouse.down();
  for (const [x, y] of points.slice(1)) {
    await page.mouse.move(box.x + box.width * x, box.y + box.height * y, { steps: 12 });
  }
  await page.mouse.up();
}

let browser;
let kioskContext;
let token;
let originalSettings;
try {
  const login = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: USERNAME, password: PASSWORD },
  });
  token = login.access_token;
  const fixtures = await prepareFixtures(token);
  originalSettings = fixtures.originalSettings;
  const latteName = fixtures.latte.name;
  ok("API 備妥會員（購物金 $500）與拿鐵 $150", true, latteName);

  browser = await chromium.launch();
  const installationId = randomUUID();
  const paired = await pairKiosk(browser, token, installationId);
  kioskContext = paired.kioskContext;
  const kioskPage = paired.kioskPage;
  ok("真客顯裝置已登入並配對本次 POS 櫃檯", true);

  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on("pageerror", (error) => ok("頁面沒有未捕捉例外", false, String(error)));
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 15_000 });
  await page.evaluate((value) => {
    window.localStorage.setItem("lu-camp.pos-terminal.installation", value);
  }, installationId);
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.locator(".pos-kiosk-status", { hasText: "顧客螢幕已連線" }).waitFor({
    state: "visible",
    timeout: 12_000,
  });

  // 純餐點：拿鐵 2 杯、外帶
  await page.locator(".pos-menu-tile").filter({ hasText: latteName }).first().click();
  const addDialog = page.getByRole("dialog", { name: new RegExp(`加入 ${latteName}`) });
  await addDialog.waitFor();
  await addDialog.getByRole("button", { name: "+" }).click().catch(() => {});
  await addDialog.getByRole("button", { name: "加入購物車" }).click();
  const total = page.locator(".pos-total", { hasText: "$300" });
  if (!(await total.isVisible().catch(() => false))) {
    // 數量彈窗沒有「＋」時再點一次磚，湊成 2 杯
    await page.locator(".pos-menu-tile").filter({ hasText: latteName }).first().click();
    await addDialog.waitFor();
    await addDialog.getByRole("button", { name: "加入購物車" }).click();
  }
  await total.waitFor({ timeout: 8_000 });
  await page.getByRole("radio", { name: "外帶" }).click();
  ok("POS 購物車只有餐點：拿鐵 2 杯 $300、外帶", true);

  await page.getByPlaceholder("姓名或電話").fill(fixtures.member.name);
  await page.getByRole("button", { name: new RegExp(fixtures.member.name) }).first().click();
  await page.locator(".pos-member-selected", { hasText: "$500" }).waitFor();

  await page.locator(".pos-tender-mode", { hasText: "購物金＋其他付款" }).click();
  await page.locator('label:has-text("本次使用購物金") input').fill("100");
  await page.locator(".pos-mixed-method", { hasText: "現金" }).click();
  const split = page.locator('[aria-label="付款金額拆分"]');
  await split.getByText(/購物金\s*\$100/).waitFor({ timeout: 8_000 });
  ok(
    "純餐點可用購物金：拆分顯示購物金 $100、剩餘 $200",
    await split.getByText(/剩餘應付\s*\$200/).isVisible(),
  );
  ok(
    "畫面沒有「餐飲不可用購物金」阻擋",
    (await page.getByText(/不可用購物金/).count()) === 0,
  );
  await page.screenshot({ path: resolve(SHOTS, "01-pos-food-store-credit.png"), fullPage: true });

  const sendForSignature = page.getByRole("button", { name: "送至手持裝置簽署" });
  await sendForSignature.waitFor({ state: "visible" });
  await page.waitForFunction(() => {
    const button = Array.from(document.querySelectorAll("button")).find(
      (candidate) => candidate.textContent?.trim() === "送至手持裝置簽署",
    );
    return button instanceof HTMLButtonElement && !button.disabled;
  });
  await sendForSignature.click();
  await kioskPage.waitForSelector('h1:has-text("購物金使用確認")', { timeout: 8_000 });
  await kioskPage.waitForSelector("canvas.kiosk-sign-canvas", { timeout: 8_000 });
  await kioskPage.screenshot({ path: resolve(SHOTS, "02-kiosk-confirm.png"), fullPage: true });
  await drawSignature(kioskPage);
  await kioskPage.locator("button.kiosk-submit").click();
  await kioskPage.waitForSelector('h1:has-text("已完成簽署")', { timeout: 8_000 });
  ok("客人在真客顯簽署購物金使用", true);
  await page.getByText(/客人已完成簽署/).waitFor({ timeout: 8_000 });

  const saleResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/sales") &&
      response.request().method() === "POST" &&
      response.ok(),
  );
  await page.locator("button.pos-checkout").click();
  const sale = await (await saleResponsePromise).json();
  await page.locator(".pos-complete", { hasText: `#${sale.id}` }).waitFor();
  const saleTenders = tenderMap(sale.tenders);
  ok(
    "POS 成立純餐點銷售：購物金 $100＋現金 $200",
    Number(sale.total) === 300 && saleTenders.STORE_CREDIT === 100 && saleTenders.CASH === 200,
    `sale=${sale.id}`,
  );
  await page.screenshot({ path: resolve(SHOTS, "03-pos-complete.png"), fullPage: true });

  // 退 1 杯：購物金先退（餐點那份 $100），其餘 $50 退現金
  await page.goto(`${BASE}/fnb-sales`, { waitUntil: "networkidle" });
  const row = page.locator("tr", { hasText: `#${sale.id}` });
  await row.waitFor();
  await row.getByRole("button", { name: `餐點退款 ${sale.id}` }).click();
  const dialog = page.getByRole("dialog", { name: "餐點退款" });
  await dialog.waitFor();
  await dialog.getByLabel(`${latteName} 退貨數量`).fill("1");
  await dialog.getByLabel("退貨原因").fill("餐飲購物金 E2E");
  const legs = dialog.getByLabel("預估退款去向");
  await legs.getByText(/購物金\s*\$100/).waitFor({ timeout: 8_000 });
  ok(
    "預估退款去向：購物金 $100＋現金 $50",
    await legs.getByText(/現金\s*\$50/).isVisible(),
    (await legs.textContent()) ?? "",
  );
  await page.screenshot({ path: resolve(SHOTS, "04-fnb-refund-preview.png"), fullPage: true });

  const returnResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/returns") &&
      response.request().method() === "POST" &&
      response.ok(),
  );
  await dialog.getByRole("button", { name: "確認退款 $150" }).click();
  const ret = await (await returnResponsePromise).json();
  await page.getByRole("status").filter({ hasText: "已退款" }).waitFor();
  const refund = tenderMap(ret.refund_tenders);
  ok(
    "實際退款：購物金 $100＋現金 $50",
    refund.STORE_CREDIT === 100 && refund.CASH === 50,
    JSON.stringify(ret.refund_tenders),
  );
  const credit = await apiJson(`/api/v1/contacts/${fixtures.member.id}/store-credit`, { token });
  ok("會員購物金回到 $500", Number(credit.balance) === 500, credit.balance);
  await page.screenshot({ path: resolve(SHOTS, "05-fnb-refunded.png"), fullPage: true });
} catch (error) {
  ok("流程中斷", false, String(error));
  if (browser) {
    const pages = browser.contexts().flatMap((context) => context.pages());
    const page = pages.at(-1);
    if (page) {
      await page
        .screenshot({ path: resolve(SHOTS, "99-failure.png"), fullPage: true })
        .catch(() => {});
    }
  }
} finally {
  if (token && originalSettings) {
    await apiJson("/api/v1/settings", {
      method: "PATCH",
      token,
      body: {
        einvoice_enabled: originalSettings.einvoice_enabled,
        store_credit_min_spend: originalSettings.store_credit_min_spend,
      },
    }).catch((error) => ok("還原測試前設定", false, String(error)));
  }
  if (kioskContext) await kioskContext.close();
  if (browser) await browser.close();
}

const failed = checks.filter((check) => !check.pass);
console.log(`\n結果：${checks.length - failed.length}/${checks.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
