// 購物金撥款的收購憑證聯補印煙霧：客人在顧客螢幕簽名選購物金 → 完成收購 →「更多操作」用單號補印 →
// 送去印的內容有撥入購物金（含溢價、與第一次印的一樣）與撥入後總額（代理兩欄都必填，原本補印一定被擋）。
// 需 backend + frontend 已起、已 seed（dev-manager、dev-kiosk）。執行：node scripts/acquisition-reprint-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { pickGrade } from "./_acquisition.mjs";
import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "acquisition-reprint");
const RUN = String(Date.now()).slice(-6);
const SELLER = `補印會員-${RUN}`;
const CATEGORY = `補印分類${RUN}`;
const INSTALLATION = crypto.randomUUID();
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

async function drawSignature(target) {
  const canvas = target.locator("canvas.kiosk-sign-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  const pts = [[0.15, 0.5], [0.3, 0.25], [0.45, 0.7], [0.6, 0.3], [0.75, 0.6], [0.85, 0.4]];
  await target.mouse.move(box.x + box.width * pts[0][0], box.y + box.height * pts[0][1]);
  await target.mouse.down();
  for (const [fx, fy] of pts.slice(1)) {
    await target.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 12 });
  }
  await target.mouse.up();
}

const browser = await chromium.launch();
const kiosk = await browser.newPage({ viewport: { width: 834, height: 1112 } });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
await page.addInitScript((id) => {
  window.localStorage.setItem("lu-camp.pos-terminal.installation", id);
}, INSTALLATION);
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const mgr = (await api(null, "POST", "/api/v1/auth/login", { username: "dev-manager", password: "dev-test-123456" })).json.access_token;
  await api(mgr, "POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  await api(mgr, "POST", "/api/v1/categories", { name: CATEGORY });
  const member = (await api(mgr, "POST", "/api/v1/contacts", {
    name: SELLER,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER", "MEMBER"],
  })).json;

  await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await kiosk.fill('input[name="username"]', "dev-kiosk");
  await kiosk.fill('input[name="password"]', "dev-test-123456");
  await kiosk.click('button:has-text("啟用裝置")');
  await kiosk.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  const code = (await kiosk.textContent(".kiosk-pairing-code"))?.trim();
  const terminal = await api(mgr, "POST", "/api/v1/customer-display/terminals", {
    installation_id: INSTALLATION,
    name: `補印煙霧 ${RUN}`,
  });
  await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminal.json.id}/pair`, { pairing_code: code });

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  const receipts = [];
  await page.route("**/print/acquisition", (route) => {
    receipts.push(route.request().postDataJSON());
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
  });
  await page.route("**/print/label", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' }),
  );
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });

  // 會員賣方、一件帳篷收 1000
  const search = page.getByPlaceholder("以手機或姓名搜尋");
  await search.fill(SELLER);
  await page.getByRole("button", { name: new RegExp(SELLER) }).first().click();
  await page.locator('.acq-row summary:has-text("品名")').first().click();
  await page.fill('input[aria-label="品名"]', "雙人帳篷");
  await pickGrade(page, "A");
  const cat = page.locator(".acq-row").first().getByLabel("分類");
  await cat.click();
  await cat.fill(CATEGORY);
  await page.locator(".acq-row").first().getByRole("option", { name: CATEGORY, exact: true }).click();
  await page.fill('input[aria-label="收購價"]', "1000");
  await page.fill('input[aria-label="上架售價（含稅與手續費）"]', "3000");

  await page.getByRole("button", { name: "送至手持裝置簽署" }).click();
  await kiosk.waitForSelector("button.kiosk-payout-btn", { timeout: 10000 });
  await kiosk.check('.kiosk-agree-check input[type="checkbox"]');
  await kiosk.click('button.kiosk-payout-btn:has-text("購物金")');
  await drawSignature(kiosk);
  await kiosk.click("button.kiosk-submit");
  await page.waitForSelector("text=客人已完成簽署", { timeout: 10000 });
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 10000 });
  const done = await page.locator(".acq-result").innerText();
  const acquisitionId = /#(\d+)/.exec(done)?.[1];
  await page.getByRole("button", { name: "列印收購憑證聯（含簽名）" }).click();
  await page.waitForTimeout(800);
  const firstPrint = receipts[0];
  ok("第一次列印：購物金兩欄都有", firstPrint?.store_credit_granted != null && firstPrint?.store_credit_balance_after != null, JSON.stringify({ g: firstPrint?.store_credit_granted, b: firstPrint?.store_credit_balance_after }));

  // 補印
  await page.locator("summary", { hasText: "更多操作" }).click();
  await page.getByLabel("要補印的收購單號").fill(acquisitionId);
  await page.getByRole("button", { name: "補印", exact: true }).click();
  await page.getByText(`已送出 #${acquisitionId} 的收購憑證聯。`).waitFor({ timeout: 8000 });
  const reprint = receipts[1];
  ok(
    "補印送出、購物金撥入額（含溢價）與撥入後總額都跟第一次一樣",
    reprint !== undefined &&
      reprint.store_credit_granted === firstPrint.store_credit_granted &&
      reprint.store_credit_balance_after === firstPrint.store_credit_balance_after &&
      reprint.payout_method === "STORE_CREDIT",
    JSON.stringify({ g: reprint?.store_credit_granted, b: reprint?.store_credit_balance_after }),
  );
  ok("撥入額含溢價（比收購價 1000 多）", Number(reprint?.store_credit_granted) > 1000, reprint?.store_credit_granted);
  await page.screenshot({ path: join(SHOTS, "01-reprinted.png"), fullPage: true });
  void member;

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  console.log(String(error));
  process.exitCode = 1;
} finally {
  await browser.close();
}
