// E2E smoke for 門市活動管理 UI（/campaigns；docs/21 C3）。
// 以 dev-manager 登入 → /campaigns → 建立活動 → 啟用 → 斷言狀態 ACTIVE → 截圖。
// 依 docs/20 配方執行；BASE_URL / SMOKE_SHOTS / SEED_USER(_PASSWORD) 由 env 帶入。
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-shots/campaigns";
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
let passed = 0;
const total = 6;

try {
  // -- 1. 登入 --
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"], input#username', USER);
  await page.fill('input[name="password"], input#password', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });
  console.log("logged in, at", page.url());
  passed++;

  // -- 2. 導航到活動管理頁 --
  await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  const pageText = await page.innerText("body");
  assert(pageText.includes("門市活動"), "頁面標題渲染");
  assert(pageText.includes("建立活動"), "建立表單渲染");
  await page.screenshot({ path: `${SHOTS}/01-campaigns-empty.png`, fullPage: true });
  console.log("  shot: 01-campaigns-empty.png");
  passed++;

  // -- 3. 填寫建立活動表單 --
  // 活動名稱
  await page.fill('input[placeholder="例如：開幕九折"]', "煙霧測試九折");
  // 折扣 %
  await page.fill('input[type="number"][min="1"]', "10");
  // 開始時間 - use a future datetime
  await page.fill('input[type="datetime-local"]:first-of-type', "2026-07-01T00:00");
  // 結束時間
  const dateInputs = page.locator('input[type="datetime-local"]');
  await dateInputs.nth(1).fill("2026-07-31T23:59");

  await page.screenshot({ path: `${SHOTS}/02-campaigns-form-filled.png`, fullPage: true });
  console.log("  shot: 02-campaigns-form-filled.png");
  passed++;

  // -- 4. 送出表單 --
  await page.click('button:has-text("建立活動")');
  await page.waitForTimeout(2000);

  // 應該在清單中看到新建的活動
  const afterCreate = await page.innerText("body");
  assert(afterCreate.includes("煙霧測試九折"), "建立後清單出現活動名稱");
  assert(afterCreate.includes("草稿"), "新活動狀態為草稿");
  await page.screenshot({ path: `${SHOTS}/03-campaigns-created.png`, fullPage: true });
  console.log("  shot: 03-campaigns-created.png");
  passed++;

  // -- 5. 啟用活動 --
  await page.click('button:has-text("啟用")');
  await page.waitForTimeout(2000);

  const afterActivate = await page.innerText("body");
  assert(afterActivate.includes("生效中"), "啟用後狀態為生效中");
  await page.screenshot({ path: `${SHOTS}/04-campaigns-activated.png`, fullPage: true });
  console.log("  shot: 04-campaigns-activated.png");
  passed++;

  // -- 6. 結束活動（清理） --
  const endBtn = page.locator('button:has-text("結束")');
  if ((await endBtn.count()) > 0) {
    await endBtn.click();
    await page.waitForTimeout(2000);
    const afterEnd = await page.innerText("body");
    assert(afterEnd.includes("已結束"), "結束後狀態為已結束");
    await page.screenshot({ path: `${SHOTS}/05-campaigns-ended.png`, fullPage: true });
    console.log("  shot: 05-campaigns-ended.png");
  }
  passed++;

  console.log(`\nSMOKE PASS (${passed}/${total})`);
} catch (e) {
  failed = true;
  console.error(`\nSMOKE FAIL (${passed}/${total}):`, e.message);
  try {
    await page.screenshot({ path: `${SHOTS}/99-failure.png`, fullPage: true });
  } catch {}
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
