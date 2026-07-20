// E2E smoke for /backup (MANAGER-only backup dashboard, docs/31 §5).
// Logs in as dev-manager against the REAL backend, asserts the health/settings/runs
// cards + two-key custody warning render, and that the manual trigger honestly
// surfaces "not configured" (503) when R2 creds are absent (dev has none) — i.e. it
// never fakes a success. Also asserts CLERK is blocked. Screenshots to SMOKE_SHOTS.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE_URL ?? process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-shots/backup";
const USER = process.env.SEED_USER ?? "dev-manager";
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";
const CLERK = process.env.CLERK_USER;

mkdirSync(SHOTS, { recursive: true });

function assert(cond, msg) {
  if (!cond) throw new Error("ASSERT FAILED: " + msg);
  console.log("  ok: " + msg);
}

async function assertStickyPanelClearsHeader(page, path, selector, label) {
  await page.setViewportSize({ width: 1280, height: 700 });
  await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
  await page.locator(selector).waitFor();
  // 空白 E2E DB 的頁面內容可能不夠長；模擬實際有大量交易／採購資料時的捲動高度。
  await page.evaluate((panelSelector) => {
    const main = document.querySelector(".app-main");
    if (main instanceof HTMLElement) main.style.minHeight = "1800px";
    const panel = document.querySelector(panelSelector);
    if (panel?.parentElement instanceof HTMLElement) panel.parentElement.style.minHeight = "1600px";
    window.scrollTo(0, 600);
  }, selector);
  await page.waitForTimeout(200);
  const geometry = await page.evaluate((panelSelector) => {
    const header = document.querySelector(".app-header")?.getBoundingClientRect();
    const panel = document.querySelector(panelSelector)?.getBoundingClientRect();
    return header && panel ? { headerBottom: header.bottom, panelTop: panel.top } : null;
  }, selector);
  assert(
    geometry !== null && geometry.panelTop >= geometry.headerBottom + 8,
    `${label} sticky panel stays below the fixed header` +
      (geometry ? ` (panel=${geometry.panelTop}, header=${geometry.headerBottom})` : ""),
  );
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
const page = await ctx.newPage();
page.on("console", (m) => {
  if (m.type() === "error") console.log("  [browser console.error]", m.text());
});
let failed = false;
try {
  // --- login as manager ---
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"], input#username', USER);
  await page.fill('input[name="password"], input#password', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });
  console.log("logged in, at", page.url());

  // --- nav: /backup lives in the left-side system menu (managerOnly) ---
  const menuButton = page.locator('button[aria-label="開啟系統選單"]');
  const menuBox = await menuButton.boundingBox();
  assert(menuBox !== null && menuBox.x <= 24 && menuBox.y <= 12, "system menu stays at top-left");
  await menuButton.click();
  await page.waitForTimeout(400);
  const drawerBox = await page.locator('nav[aria-label="系統選單"]').boundingBox();
  assert(drawerBox !== null && drawerBox.x === 0, "system menu opens from the left edge");
  const drawerText = await page.innerText("body");
  assert(/備份/.test(drawerText), "備份 entry appears in system menu");
  await page.screenshot({ path: `${SHOTS}/system-menu-left.png`, fullPage: true });
  console.log("  shot: system-menu-left.png");
  await page.click('nav[aria-label="系統選單"] a:has-text("備份")');
  await page.waitForURL((u) => u.pathname.endsWith("/backup"), { timeout: 10000 });
  await page.waitForTimeout(1200);

  // --- /backup dashboard renders ---
  const body = await page.innerText("body");
  assert(/備份健康度/.test(body), "/backup renders 健康度 card");
  assert(/兩組金鑰缺一即廢/.test(body), "/backup shows two-key custody warning");
  assert(/備份設定/.test(body), "/backup renders 設定 card");
  assert(/保留份數/.test(body), "/backup settings has 保留份數 field");
  assert(/備份紀錄/.test(body), "/backup renders 紀錄 card");
  assert(!/讀取健康度失敗/.test(body), "health query did not error");
  await page.screenshot({ path: `${SHOTS}/backup.png`, fullPage: true });
  console.log("  shot: backup.png");

  // --- manual trigger: dev has no R2 creds → must honestly report "未設定" (503), not fake success ---
  const triggerBtn = page.locator('button:has-text("立即備份")');
  assert((await triggerBtn.count()) > 0, "立即備份 button present");
  await triggerBtn.first().click();
  await page.waitForTimeout(1500);
  const afterTrigger = await page.innerText("body");
  assert(/未設定|尚未設定|R2/.test(afterTrigger), "manual trigger surfaces 未設定 (503), no fake success");
  await page.screenshot({ path: `${SHOTS}/backup-trigger-unconfigured.png`, fullPage: true });
  console.log("  shot: backup-trigger-unconfigured.png");

  // --- restore card: gated confirm (type filename + acknowledge), then honest 503 (no R2 in dev) ---
  // (A SUCCEEDED backup_run is seeded into the e2e DB so the source dropdown populates.)
  assert(/還原（災難復原）/.test(afterTrigger), "restore card renders");
  const restoreSelect = page.locator("select");
  if ((await restoreSelect.count()) > 0 && (await restoreSelect.locator("option").count()) > 1) {
    await restoreSelect.selectOption({ index: 1 });
    await page.click('button:has-text("還原此備份到驗證庫")');
    await page.waitForTimeout(300);
    const confirmBtn = page.locator('button:has-text("確認還原到驗證庫")');
    assert(await confirmBtn.isDisabled(), "confirm disabled before typing filename + acknowledging");
    // fill the exact filename (from the option label) and check acknowledge
    const optLabel = await restoreSelect.locator("option").nth(1).innerText();
    const fname = optLabel.split("（")[0].trim();
    await page.fill('.backup-confirm input[type="text"]', fname);
    await page.check('input[aria-label="知情同意"]');
    assert(!(await confirmBtn.isDisabled()), "confirm enabled after filename + acknowledge");
    await confirmBtn.click();
    await page.waitForTimeout(1200);
    const afterRestore = await page.innerText("body");
    assert(/未設定|R2/.test(afterRestore), "restore honestly reports 未設定 (503), no fake restore");
    await page.screenshot({ path: `${SHOTS}/backup-restore-gated.png`, fullPage: true });
    console.log("  shot: backup-restore-gated.png");
  } else {
    console.log("  (no seeded backup source; restore confirm flow skipped)");
  }

  // --- fixed header must not cover existing sticky work panels ---
  await assertStickyPanelClearsHeader(page, "/pos", ".pos-right", "POS");
  await assertStickyPanelClearsHeader(
    page,
    "/purchasing",
    ".pur-workbench-rail",
    "purchasing",
  );

  // --- desktop POS: 1024px laptop viewport must not squeeze the cart into the checkout rail ---
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  const desktopPosGeometry = await page.evaluate(() => {
    const grid = document.querySelector(".pos-grid")?.getBoundingClientRect();
    const left = document.querySelector(".pos-left")?.getBoundingClientRect();
    const right = document.querySelector(".pos-right")?.getBoundingClientRect();
    return grid && left && right
      ? {
          gridWidth: grid.width,
          leftBottom: left.bottom,
          rightTop: right.top,
          rightWidth: right.width,
          viewportWidth: document.documentElement.clientWidth,
          contentWidth: document.documentElement.scrollWidth,
        }
      : null;
  });
  assert(
    desktopPosGeometry !== null &&
      desktopPosGeometry.rightTop >= desktopPosGeometry.leftBottom + 16,
    "POS checkout rail stacks below the cart at 1024px desktop width",
  );
  assert(
    desktopPosGeometry !== null &&
      Math.abs(desktopPosGeometry.rightWidth - desktopPosGeometry.gridWidth) < 1,
    "stacked POS checkout rail uses the full desktop content width",
  );
  assert(
    desktopPosGeometry !== null &&
      desktopPosGeometry.contentWidth === desktopPosGeometry.viewportWidth,
    "POS has no horizontal overflow at 1024px desktop width",
  );
  await page.screenshot({ path: `${SHOTS}/pos-desktop-1024.png`, fullPage: true });
  console.log("  shot: pos-desktop-1024.png");

  // --- narrow POS: scan input and checkout card must stay inside the viewport ---
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  const posWidth = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  assert(
    posWidth.content === posWidth.viewport,
    `POS has no horizontal overflow at 320px (${posWidth.content}/${posWidth.viewport})`,
  );
  await page.screenshot({ path: `${SHOTS}/pos-320.png`, fullPage: true });
  console.log("  shot: pos-320.png");

  // --- mobile: the same menu control remains fixed at the top-left ---
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const mobileMenuBox = await page.locator('button[aria-label="開啟系統選單"]').boundingBox();
  assert(
    mobileMenuBox !== null && mobileMenuBox.x <= 24 && mobileMenuBox.y <= 12,
    "system menu stays at top-left on mobile",
  );
  await page.evaluate(() => window.scrollTo(0, 900));
  await page.waitForTimeout(200);
  const scrolledMenuBox = await page.locator('button[aria-label="開啟系統選單"]').boundingBox();
  assert(
    scrolledMenuBox !== null && scrolledMenuBox.x <= 24 && scrolledMenuBox.y <= 12,
    "system menu remains fixed after scrolling",
  );
  await page.screenshot({ path: `${SHOTS}/system-menu-mobile.png` });
  console.log("  shot: system-menu-mobile.png");

  // --- CLERK blocked (fresh context, server-driven gate on MANAGER-only health) ---
  if (CLERK) {
    const clerkCtx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const cp = await clerkCtx.newPage();
    await cp.goto(`${BASE}/login`, { waitUntil: "networkidle" });
    await cp.fill('input[name="username"], input#username', CLERK);
    await cp.fill('input[name="password"], input#password', PASS);
    await cp.click('button[type="submit"]');
    await cp.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });
    await cp.goto(`${BASE}/backup`, { waitUntil: "networkidle" });
    await cp.waitForTimeout(1000);
    const t = await cp.innerText("body");
    assert(/需管理者權限/.test(t), "CLERK blocked from /backup (需管理者權限)");
    assert(!/兩組金鑰|備份設定/.test(t), "CLERK sees no backup manager content");
    await cp.screenshot({ path: `${SHOTS}/backup-clerk-blocked.png`, fullPage: true });
    console.log("  shot: backup-clerk-blocked.png");
    await clerkCtx.close();
  }

  console.log("\nSMOKE PASS");
} catch (e) {
  failed = true;
  console.error("\nSMOKE FAIL:", e.message);
  try {
    await page.screenshot({ path: `${SHOTS}/failure.png`, fullPage: true });
  } catch {}
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
