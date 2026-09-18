// 客顯「解除配對 → 換裝置 → 重新配對」瀏覽器煙霧（docs/08 §6.1）。
//
// 守的是 2026-09-18 的兩個回報：
//   1. 客顯登入後幾秒就被登出（Secure cookie 在 HTTP 內網被瀏覽器拒存）——這支從真
//      瀏覽器登入後停留 8 秒以上，確認沒有被踢回登入畫面。
//   2. 客顯斷線後無法再配對（POS 端完全沒有解除配對的入口）——這支在 POS 畫面上實際
//      按下「解除配對」，確認能回到輸入配對碼的畫面並用新平板重新配對成功。
//
// 執行：node scripts/kiosk-unpair-smoke.mjs
//   需 backend:8000 + frontend:3000 對真 Postgres 跑，且 SMOKE_* 帳號可登入。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

// 前端與 API 必須同站（same-site），否則 SameSite=strict 的裝置 cookie 根本不會被送出，
// 測出來的「被登出」是煙霧環境自己造成的假象。正式機兩者同 host、只差 port＝同站，
// 所以這裡預設也讓 BASE 跟著 API 的 host 走。
const API_BASE = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const BASE = (
  process.env.SMOKE_BASE ?? `${new URL(API_BASE).protocol}//${new URL(API_BASE).hostname}:3000`
).replace(/\/+$/, "");
if (new URL(BASE).hostname !== new URL(API_BASE).hostname) {
  console.error(
    `SMOKE_BASE (${BASE}) 與 SMOKE_API_BASE (${API_BASE}) 不同 host＝跨站，` +
      "SameSite=strict 的客顯 cookie 不會被送出，這支煙霧會測出假的「被登出」。",
  );
  process.exit(2);
}
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "codex-test", "kiosk-unpair-smoke");
const MGR_USER = process.env.SMOKE_USERNAME ?? "dev-manager";
const MGR_PASS = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const KIOSK_USER = process.env.SMOKE_KIOSK_USERNAME ?? "dev-kiosk";
const KIOSK_PASS = process.env.SMOKE_KIOSK_PASSWORD ?? "dev-test-123456";
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiLogin(username, password) {
  const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(`login ${username} failed: ${res.status}`);
  return (await res.json()).access_token;
}

/** 開一台「實體平板」：獨立 context → 獨立 cookie jar 與 localStorage。 */
async function openKiosk(browser, label) {
  const context = await browser.newContext({ viewport: { width: 834, height: 1112 } });
  const page = await context.newPage();
  await page.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', KIOSK_USER);
  await page.fill('input[name="password"]', KIOSK_PASS);
  await page.fill('input[name="label"]', label);
  await page.click('button:has-text("啟用裝置")');
  await page.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  return { context, page };
}

const browser = await chromium.launch();
try {
  // 只為了確認店務帳號真的能登入（POS 頁用的是畫面登入，不是這個 token）。
  await apiLogin(MGR_USER, MGR_PASS);

  // ── 1. 舊平板登入並配對 ────────────────────────────────────────────────
  const oldTablet = await openKiosk(browser, "煙霧舊平板");
  const firstCode = (await oldTablet.page.textContent(".kiosk-pairing-code"))?.trim();
  await oldTablet.page.screenshot({ path: join(SHOTS, "01-kiosk-pairing-code.png") });

  // 回歸守門（回報 1）：登入後停留 8 秒——device 狀態每 5 秒輪詢一次，Secure cookie
  // 若又被瀏覽器拒存，這裡就會退回登入表單。
  await oldTablet.page.waitForTimeout(8000);
  const stillPairingScreen = await oldTablet.page
    .locator(".kiosk-pairing-code")
    .isVisible()
    .catch(() => false);
  ok(
    "客顯登入後 8 秒仍停在配對畫面（未被登出）",
    stillPairingScreen,
    stillPairingScreen ? "" : "已被踢回登入畫面＝cookie 又存不進去了",
  );
  await oldTablet.page.screenshot({ path: join(SHOTS, "02-kiosk-still-logged-in.png") });

  // ── 2. 開 POS，並**用畫面上的配對碼欄位**把舊平板配對上去（真實流程）────
  // POS 瀏覽器會用自己的 installation_id 註冊一台櫃檯，不能用 API 另外建一台再配對，
  // 否則 POS 畫面看到的仍是「尚未配對」。
  const posContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const pos = await posContext.newPage();
  await pos.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await pos.fill('input[name="username"]', MGR_USER);
  await pos.fill('input[name="password"]', MGR_PASS);
  await pos.click('button[type="submit"]');
  await pos.waitForURL(/\/(pos|opening-check|$)/, { timeout: 10000 });
  await pos.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await pos.waitForSelector('strong:has-text("顧客螢幕尚未配對")', { timeout: 10000 });
  await pos.fill('.pos-kiosk-status input[inputmode="numeric"]', firstCode ?? "");
  await pos.click('.pos-kiosk-status button:has-text("配對")');
  // 等「解除配對」出現才算真的配上了：不能等含「顧客螢幕」的字串——未配對畫面的
  // 「顧客螢幕尚未配對」也含這四個字，等待會立刻假性成立。
  await pos
    .waitForSelector('.pos-kiosk-status button:has-text("解除配對")', { timeout: 15000 })
    .catch(() => null);
  const firstPairText = (await pos.textContent(".pos-kiosk-status")) ?? "";
  ok(
    "舊平板由 POS 畫面配對成功",
    firstPairText.includes("已連線") || firstPairText.includes("離線"),
    firstPairText.trim(),
  );

  const unpairButton = pos.locator('button:has-text("解除配對")');
  const hasUnpairEntry = await unpairButton.first().isVisible().catch(() => false);
  ok(
    "POS 已配對時看得到「解除配對」入口",
    hasUnpairEntry,
    hasUnpairEntry ? "" : "找不到入口＝斷線後仍無法重新配對",
  );
  await pos.screenshot({ path: join(SHOTS, "03-pos-unpair-entry.png"), fullPage: true });

  if (hasUnpairEntry) {
    await unpairButton.first().click();
    // 標籤必須是螢幕閱讀器專用，不能變成畫面上的一般文字（.sr-only 曾經沒定義）。
    const reasonInput = pos.locator('input[placeholder*="解除配對原因"]');
    await reasonInput.fill("煙霧測試換裝置");
    await pos.screenshot({ path: join(SHOTS, "04-pos-unpair-form.png"), fullPage: true });
    await pos.click('button:has-text("確認解除配對")');
    await pos.waitForSelector('strong:has-text("顧客螢幕尚未配對")', { timeout: 8000 });
    ok("解除配對後回到輸入配對碼畫面", true);
    await pos.screenshot({ path: join(SHOTS, "05-pos-back-to-pairing.png"), fullPage: true });

    // ── 3. 換一台新平板，從 POS 畫面重新配對 ──────────────────────────────
    const newTablet = await openKiosk(browser, "煙霧新平板");
    const secondCode = (await newTablet.page.textContent(".kiosk-pairing-code"))?.trim();
    ok("新平板拿到新的配對碼", Boolean(secondCode) && secondCode !== firstCode);
    await pos.fill('.pos-kiosk-status input[inputmode="numeric"]', secondCode ?? "");
    await pos.click('.pos-kiosk-status button:has-text("配對")');
    await pos
      .waitForSelector('.pos-kiosk-status button:has-text("解除配對")', { timeout: 15000 })
      .catch(() => null);
    const repairedText = (await pos.textContent(".pos-kiosk-status")) ?? "";
    const repaired = repairedText.includes("已連線") || repairedText.includes("離線");
    ok("以新平板重新配對成功", repaired, repairedText.trim());
    await pos.screenshot({ path: join(SHOTS, "06-pos-repaired.png"), fullPage: true });
    await newTablet.page.screenshot({ path: join(SHOTS, "07-new-kiosk-paired.png") });
    await newTablet.context.close();
  }

  await posContext.close();
  await oldTablet.context.close();
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n截圖：${SHOTS}`);
console.log(`${results.length - failed.length}/${results.length} 通過`);
if (failed.length > 0) process.exit(1);
