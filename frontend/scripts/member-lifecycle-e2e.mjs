// 會員生命週期 E2E：建檔防呆（手機必填唯一、身分證檢核）＋ 只買會員日後來賣的「以手機查找 → 補登身分證」流程。
// 真實情境：
//   1) 建立只買不賣的會員（手機、無身分證）。
//   2) 同手機再建一人 → 被擋（手機同店唯一）。
//   3) 身分證字號打錯 → 被擋（檢核碼）。
//   4) 該會員日後來賣二手 → 收購頁以「手機」查找既有會員 → 顯示「尚未建檔身分證」→ 補登身分證+設為賣方。
//   5) 補登時身分證打錯 → 被擋。
// 執行：node scripts/member-lifecycle-e2e.mjs（需 backend:8000 + frontend:3000 已起、dev-manager 可登入）。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "codex-test", "member-lifecycle");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const RUN = Date.now().toString().slice(-6);
const MEMBER = `生命週期會員 ${RUN}`;
const PHONE = uniquePhone(Number(RUN));

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await login(page);

  // ── /contacts 建檔 ──
  await page.goto(`${BASE}/contacts`);
  await page.waitForSelector('button:has-text("建檔")');

  // 1) 只買不賣的會員：姓名 + 手機，無身分證 → 成功
  await page.getByLabel("姓名 *").fill(MEMBER);
  await page.getByLabel("電話 *").fill(PHONE);
  await page.click('button:has-text("建檔")');
  await page.waitForFunction(
    () => !document.querySelector('[role="alert"].form-error'),
    undefined,
    { timeout: 8000 },
  );
  ok("1) 建立只買會員（手機、無身分證）", true, PHONE);
  await page.screenshot({ path: `${SHOTS}/01-member-created.png` });

  // 2) 同手機再建一人 → 手機同店唯一被擋（409）
  await page.getByLabel("姓名 *").fill(`撞號 ${RUN}`);
  await page.getByLabel("電話 *").fill(PHONE);
  await page.click('button:has-text("建檔")');
  const dupErr = page.locator('[role="alert"].form-error', { hasText: /此手機號碼已有聯絡人/ });
  await dupErr.waitFor({ state: "visible", timeout: 8000 });
  ok("2) 同手機建檔 → 被擋（手機唯一）", true, (await dupErr.textContent()) ?? "");
  await page.screenshot({ path: `${SHOTS}/02-duplicate-phone-blocked.png` });

  // 3) 身分證字號打錯 → 檢核擋下
  await page.getByLabel("姓名 *").fill(`證號錯 ${RUN}`);
  await page.getByLabel("電話 *").fill(uniquePhone());
  await page.getByLabel("身分證字號（收購/寄售必填）").fill("A123456788"); // 末碼錯
  await page.click('button:has-text("建檔")');
  const nidErr = page.locator('[role="alert"].form-error', { hasText: /身分證字號格式或檢核碼不正確/ });
  await nidErr.waitFor({ state: "visible", timeout: 8000 });
  ok("3) 身分證字號打錯 → 被擋（檢核碼）", true);
  await page.screenshot({ path: `${SHOTS}/03-invalid-nid-blocked.png` });

  // ── /acquisition 收購：以手機查找既有會員 → 補登身分證 ──
  await page.goto(`${BASE}/acquisition`);
  await page.waitForSelector(".acq-search");
  const search = page.getByLabel("賣方搜尋", { exact: true });
  await search.fill(PHONE); // 以手機查找
  await page.waitForTimeout(500);
  await page.locator(".acq-results .combo-option").filter({ hasText: MEMBER }).first().click();
  await page.getByText("尚未建檔身分證").waitFor({ state: "visible", timeout: 8000 });
  ok("4a) 收購以手機查到既有會員、顯示尚未建檔身分證", true);
  await page.screenshot({ path: `${SHOTS}/04-found-by-phone-needs-nid.png` });

  // 5) 補登時打錯 → 被擋
  await page.getByLabel("補登身分證字號").fill("A123456788");
  await page.click('button:has-text("補登並設為賣方")');
  const backfillErr = page.locator('[role="alert"].form-error', { hasText: /身分證字號格式或檢核碼不正確/ });
  await backfillErr.waitFor({ state: "visible", timeout: 8000 });
  ok("5) 補登身分證打錯 → 被擋", true);
  await page.screenshot({ path: `${SHOTS}/05-backfill-invalid-blocked.png` });

  // 4b) 補登正確身分證 → 升級為賣方（已建檔）
  await page.getByLabel("補登身分證字號").fill(validNationalId(Number(RUN)));
  await page.click('button:has-text("補登並設為賣方")');
  await page.getByText("（已建檔）").waitFor({ state: "visible", timeout: 8000 });
  ok("4b) 補登正確身分證 → 升級為賣方（已建檔）", true);
  await page.screenshot({ path: `${SHOTS}/06-backfilled-as-seller.png` });
} finally {
  await browser.close();
}

const passed = results.filter((r) => r.pass).length;
console.log(`\n結果：${passed}/${results.length} 通過\n截圖：${SHOTS}`);
if (passed !== results.length) process.exit(1);
