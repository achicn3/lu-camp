// K4 收購×手持切結整合煙霧（docs/23）：店員於收購頁鑑價 → 送至手持裝置 → 客人在顧客螢幕頁
// 勾同意、選撥款、簽名 → 店員完成收購 → 驗證撥款＝客人所選、切結用過即作廢（CONSUMED）、不能重複綁定。
// 2026-09-26 更新：先把顧客螢幕與這台櫃檯配對（現在送簽一定要配對且在線）、改在顧客螢幕頁實際簽名、
// 等待字樣改成現行文字；綁定後任務狀態由 SIGNED 改為 CONSUMED（單次使用，見 acquisition service）。
// 需 backend:8000 + frontend:3000 + 硬體代理（假機即可，需 AGENT_BACKEND_URL，見 docs/20 §3.1）、dev-manager + dev-kiosk 已 seed。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { fillItemName, pickGrade } from "./_acquisition.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "codex-test", "kiosk-acq-smoke");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiLogin(u, p) {
  const r = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username: u, password: p }),
  });
  return (await r.json()).access_token;
}


const browser = await chromium.launch();
const INSTALLATION = crypto.randomUUID();
const kioskPage = await browser.newPage({ viewport: { width: 834, height: 1112 } });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.addInitScript((id) => {
  window.localStorage.setItem("lu-camp.pos-terminal.installation", id);
}, INSTALLATION);
page.on("pageerror", (e) => ok("頁面 JS 錯誤", false, String(e)));

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

/** 客人在顧客螢幕頁簽名：勾同意、選撥款、簽名、送出。 */
async function customerSigns(payoutLabel) {
  await kioskPage.waitForSelector("button.kiosk-payout-btn", { timeout: 10000 });
  await kioskPage.check('.kiosk-agree-check input[type="checkbox"]');
  await kioskPage.click(`button.kiosk-payout-btn:has-text("${payoutLabel}")`);
  await drawSignature(kioskPage);
  await kioskPage.click("button.kiosk-submit");
}

/** 送簽並攔下新建的簽署任務 id（之後查狀態、測重複綁定用）。 */
async function pushSignAndCaptureTask() {
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/v1/signing/tasks"),
    { timeout: 8000 },
  );
  await page.click('button:has-text("送至手持裝置簽署")');
  const task = await (await created).json();
  await page.waitForSelector("text=/已送至顧客螢幕|客人正在核對/", { timeout: 8000 });
  return task;
}

try {
  const mgr = await apiLogin("dev-manager", "dev-test-123456");

  // 顧客螢幕啟用並與這台櫃檯配對（櫃檯的安裝碼預先寫進店員頁的瀏覽器）
  await kioskPage.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await kioskPage.fill('input[name="username"]', "dev-kiosk");
  await kioskPage.fill('input[name="password"]', "dev-test-123456");
  await kioskPage.click('button:has-text("啟用裝置")');
  await kioskPage.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  const pairingCode = (await kioskPage.textContent(".kiosk-pairing-code"))?.trim();
  const terminal = await (
    await fetch(`${API}/api/v1/customer-display/terminals`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${mgr}` },
      body: JSON.stringify({ installation_id: INSTALLATION, name: `切結煙霧 ${Date.now()}` }),
    })
  ).json();
  const pairResp = await fetch(`${API}/api/v1/customer-display/terminals/${terminal.id}/pair`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${mgr}` },
    body: JSON.stringify({ pairing_code: pairingCode }),
  });
  ok("顧客螢幕與櫃檯配對", pairResp.status === 200, `status=${pairResp.status}`);

  // 開帳（CASH 收購需要）
  await fetch(`${API}/api/v1/cash-sessions/open`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${mgr}` },
    body: JSON.stringify({ opening_float: "1000" }),
  });

  // 店員：登入 → 收購頁
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.waitForSelector('[role="tab"]:has-text("買斷")');

  // 建立賣方（唯一手機避免重跑衝突）
  const nid = "A123456789";
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', "切結賣家");
  await page.fill('input[aria-label="手機"]', `09${Date.now().toString().slice(-8)}`);
  await page.fill('input[aria-label="身分證字號"]', nid);
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector("text=切結賣家");
  ok("建立並選取賣方", true);

  // 鑑價列
  await fillItemName(page, "登山外套");
  await pickGrade(page, "A");
  const brand = page.getByLabel("品牌");
  await brand.click();
  await brand.fill(`品牌${Date.now().toString().slice(-5)}`);
  await page.click('button:has-text("建立「")');
  const cat = page.getByLabel("分類");
  await cat.click();
  await cat.fill(`分類${Date.now().toString().slice(-5)}`);
  await page.click('button:has-text("建立「")');
  await page.fill('input[aria-label="收購價"]', "1200");
  await page.fill('input[aria-label="上架售價（含稅與手續費）"]', "3000");

  // 送至手持裝置簽署
  const cur = await pushSignAndCaptureTask();
  ok("送至手持裝置、等待簽署", cur && cur.kind === "ACQUISITION_AFFIDAVIT", `kind=${cur?.kind}`);
  await page.screenshot({ path: join(SHOTS, "01-pushed.png"), fullPage: true });

  // 客人在顧客螢幕上簽名（選現金）
  await customerSigns("現金");
  ok("客人在顧客螢幕簽署完成", true);

  // 店員端輪詢應轉為「已完成簽署」
  await page.waitForSelector("text=客人已完成簽署", { timeout: 10000 });
  ok("店員端顯示客人已簽署", true);
  await page.screenshot({ path: join(SHOTS, "02-signed.png"), fullPage: true });

  // 完成收購
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 10000 });
  ok("完成收購", true);
  const firstDoneText = await page.textContent(".acq-result p");
  await page.screenshot({ path: join(SHOTS, "03-done.png"), fullPage: true });

  // K6：收購憑證聯（切結品項/總額/撥款＋賣方簽名）——以網路回應為準（不受畫面殘留影響）
  const printResp1 = page.waitForResponse(
    (r) => r.url().includes("/print/acquisition") && r.status() === 200,
    { timeout: 8000 },
  );
  await page.click('button:has-text("列印收購憑證聯")');
  const printReq1 = (await printResp1).request().postDataJSON();
  ok("收購憑證聯送出列印（現金撥款）", true);
  ok(
    "現金撥款 payload 不帶購物金欄位",
    printReq1.store_credit_granted === null && printReq1.store_credit_balance_after === null,
    `granted=${printReq1.store_credit_granted} balance_after=${printReq1.store_credit_balance_after}`,
  );
  await page.screenshot({ path: join(SHOTS, "03b-receipt.png"), fullPage: true });

  // ── K6 變體：購物金撥款的收購憑證聯（撥入購物金行）───────────────────
  // 會員賣家（SELLER+MEMBER，B100000002 去重冪等）由 API 建立，UI 以電話搜尋選取。
  const memberSeller = await (
    await fetch(`${API}/api/v1/contacts`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${mgr}` },
      body: JSON.stringify({
        name: "憑證會員",
        phone: `09${Date.now().toString().slice(-8)}`,
        national_id: "B100000002",
        roles: ["SELLER", "MEMBER"],
      }),
    })
  ).json();
  // 完成收購後表單已重置（seller 已清空），直接搜尋選取會員賣家。
  await page.fill('input[aria-label="賣方搜尋"]', memberSeller.phone);
  await page.click(`.acq-results button:has-text("${memberSeller.name}")`);
  await fillItemName(page, "睡袋");
  await pickGrade(page, "A");
  const cat2 = page.getByLabel("分類");
  await cat2.click();
  await cat2.fill(`分類${Date.now().toString().slice(-5)}`);
  await page.click('button:has-text("建立「")');
  await page.fill('input[aria-label="收購價"]', "800");
  await page.fill('input[aria-label="上架售價（含稅與手續費）"]', "2000");
  await pushSignAndCaptureTask();
  await customerSigns("購物金");
  ok("購物金撥款簽署完成", true);
  // 等簽署面板轉「已完成」（面板唯一、不受流程一殘留影響）
  await page.waitForSelector('text=客人已完成簽署', { timeout: 15000 });
  await page.click('button:has-text("送出收購")');
  await page.waitForTimeout(2500);
  const errText = await page.textContent(".acq-errors").catch(() => null);
  if (errText) console.log("[diag] submit errors:", errText);
  // 等**新**單號出現（流程一的結果卡仍在畫面上，改比對文字變化）
  await page.waitForFunction(
    (prev) => {
      const el = document.querySelector(".acq-result p");
      return el && el.textContent !== prev;
    },
    firstDoneText,
    { timeout: 10000 },
  );
  const printResp2 = page.waitForResponse(
    (r) => r.url().includes("/print/acquisition") && r.status() === 200,
    { timeout: 8000 },
  );
  await page.click('button:has-text("列印收購憑證聯")');
  const printReq2 = (await printResp2).request().postDataJSON();
  ok("收購憑證聯送出列印（購物金撥款＋撥入行）", true);
  // 撥入 800×(1+premium_rate)：溢價率**動態取自 settings**（環境可能非預設 0.10，
  // 例如 sim 資料集期中調 0.12——寫死 880 會誤報系統錯）；購物金總額＝後端帳本
  // balance_after（同一會員跨執行累積，只驗為正整數字串且 ≥ 本筆實發）。
  const settingsResp = await fetch(`${API}/api/v1/settings`, {
    headers: {
      Authorization: `Bearer ${await apiLogin("dev-manager", "dev-test-123456")}`,
    },
  });
  const premiumRate = Number((await settingsResp.json()).premium_rate);
  const expectedGranted = String(Math.round(800 * (1 + premiumRate)));
  ok(
    "列印 payload 帶撥入金額與購物金總額",
    printReq2.store_credit_granted === expectedGranted &&
      /^\d+$/.test(String(printReq2.store_credit_balance_after)) &&
      Number(printReq2.store_credit_balance_after) >= Number(expectedGranted),
    `granted=${printReq2.store_credit_granted}（預期 ${expectedGranted}＝800×(1+${premiumRate})） balance_after=${printReq2.store_credit_balance_after}`,
  );
  await page.screenshot({ path: join(SHOTS, "03c-receipt-credit.png"), fullPage: true });

  // 驗證後端：任務被綁定（get task → ref 或以 sign task 查其綁定收購）。以 acquisition 反查：
  const taskAfter = await (
    await fetch(`${API}/api/v1/signing/tasks/${cur.id}`, {
      headers: { authorization: `Bearer ${mgr}` },
    })
  ).json();
  ok("切結綁定收購後即用掉（CONSUMED，單次使用）", taskAfter.status === "CONSUMED", taskAfter.status);

  // 綁定不可重複使用：以同一 task 再建收購 → 409
  const dup = await fetch(`${API}/api/v1/acquisitions`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${mgr}`,
      "Idempotency-Key": `dup-${Date.now()}`,
    },
    body: JSON.stringify({
      type: "BUYOUT",
      contact_id: cur.contact_id,
      // 與已簽切結相同內容（登山外套/1200），才會通過內容一致檢查、觸及單次使用唯一約束。
      items: [{ name: "登山外套", grade: "A", listed_price: "3000", acquisition_cost: "1200" }],
      payout_method: "CASH",
      signature_task_id: cur.id,
    }),
  });
  ok("切結單次使用（重複綁定→409）", dup.status === 409, `status=${dup.status}`);
} catch (err) {
  ok("煙霧未拋例外", false, String(err?.message ?? err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
