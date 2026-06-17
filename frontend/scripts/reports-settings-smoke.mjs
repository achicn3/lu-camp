// E2E smoke for /reports + /settings (MANAGER-only screens).
// Logs in as dev-manager, visits each page against the REAL backend,
// asserts key zh-TW content renders, and writes screenshots to SMOKE_SHOTS.
// Run inside the playwright docker image; BASE_URL/API_URL via env.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp";
const USER = process.env.SEED_USER ?? "dev-manager";
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";

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
  // --- login ---
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"], input#username', USER);
  await page.fill('input[name="password"], input#password', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });
  console.log("logged in, at", page.url());

  // --- /reports ---
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  const reportsText = await page.innerText("body");
  assert(/報表|購物金/.test(reportsText), "/reports renders zh-TW heading");
  assert(/負債/.test(reportsText), "/reports shows 負債 (liability) section");
  assert(/1,?980|報表測試客/.test(reportsText), "/reports shows seeded liability data");
  await page.screenshot({ path: `${SHOTS}/reports.png`, fullPage: true });
  console.log("  shot: reports.png");

  // --- /settings ---
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  const settingsText = await page.innerText("body");
  assert(/設定/.test(settingsText), "/settings renders zh-TW heading");
  assert(/溢價|premium/i.test(settingsText), "/settings shows premium-suggestion section");
  await page.screenshot({ path: `${SHOTS}/settings.png`, fullPage: true });
  console.log("  shot: settings.png");

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
