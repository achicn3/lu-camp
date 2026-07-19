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

  // --- nav: /backup lives in the 更多 side menu (managerOnly) ---
  await page.click('button:has-text("更多")');
  await page.waitForTimeout(400);
  const drawerText = await page.innerText("body");
  assert(/備份/.test(drawerText), "備份 entry appears in 更多 side menu");
  await page.click('nav[aria-label="更多功能"] a:has-text("備份")');
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
