// LIVE E2E: drive a REAL restore through the GUI (backend configured with R2).
// 1) login manager → /backup, 2) click 立即備份 → wait a SUCCEEDED backup row,
// 3) restore card: pick that backup, type filename + acknowledge, confirm,
// 4) wait the restore record to reach 四驗通過 (VERIFIED) with all four checks ✅.
// Proves the GUI restore actually restores to a throwaway DB and verifies. Screenshots.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE_URL ?? process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-shots/backup";
const USER = process.env.SEED_USER ?? "dev-manager";
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });
function assert(c, m) {
  if (!c) throw new Error("ASSERT FAILED: " + m);
  console.log("  ok: " + m);
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1200 } });
const page = await ctx.newPage();
page.on("console", (m) => {
  if (m.type() === "error") console.log("  [browser console.error]", m.text());
});
let failed = false;
try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"], input#username', USER);
  await page.fill('input[name="password"], input#password', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });
  await page.goto(`${BASE}/backup`, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);

  // 1) real backup via GUI (R2 configured now)
  await page.click('button:has-text("立即備份")');
  console.log("  triggered 立即備份, waiting for SUCCEEDED row…");
  await page.waitForFunction(
    () => /成功/.test(document.body.innerText) && !/備份未設定/.test(document.body.innerText),
    { timeout: 60000 },
  );
  assert(!/備份未設定/.test(await page.innerText("body")), "backup succeeded (no 未設定)");
  await page.screenshot({ path: `${SHOTS}/live-after-backup.png`, fullPage: true });

  // 2) restore the freshly-made backup
  const select = page.locator("select");
  await page.waitForFunction(
    () => document.querySelectorAll("select option").length > 1,
    { timeout: 15000 },
  );
  await select.selectOption({ index: 1 });
  const optLabel = await select.locator("option").nth(1).innerText();
  const fname = optLabel.split("（")[0].trim();
  console.log("  restoring backup:", fname);
  await page.click('button:has-text("還原此備份到驗證庫")');
  await page.fill('.backup-confirm input[type="text"]', fname);
  await page.check('input[aria-label="知情同意"]');
  await page.click('button:has-text("確認還原到驗證庫")');
  console.log("  triggered restore, waiting for 四驗通過…");

  // 3) wait for VERIFIED with the four checks rendered, then screenshot immediately
  await page.waitForFunction(
    () => /四驗通過/.test(document.body.innerText) && /alembic_head/.test(document.body.innerText),
    { timeout: 90000 },
  );
  await page.screenshot({ path: `${SHOTS}/live-restore-verified.png`, fullPage: true });
  console.log("  shot: live-restore-verified.png");
  const body = await page.innerText("body");
  assert(/四驗通過/.test(body), "restore reached 四驗通過 (VERIFIED) in the GUI");
  assert(/alembic_head/.test(body), "shows alembic_head check");
  assert(/table_counts/.test(body), "shows table_counts check");
  assert(/backend_usable/.test(body), "shows backend_usable check");
  assert(!/❌/.test(body.split("還原紀錄")[1] ?? ""), "no failed (❌) checks in restore record");

  console.log("\nSMOKE PASS");
} catch (e) {
  failed = true;
  console.error("\nSMOKE FAIL:", e.message);
  try {
    await page.screenshot({ path: `${SHOTS}/live-failure.png`, fullPage: true });
  } catch {}
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
