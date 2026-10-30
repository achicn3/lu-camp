// POS 右側結帳面板固定煙霧：購物車品項多、餐飲菜單長時，右側面板（統編／載具／收款／結帳鈕）
// 要一直留在畫面內，不必把左欄整個捲完。需 backend+frontend 已起、已開帳、至少 8 個可售
// menu_item；要驗載具欄須先啟用電子發票（未啟用則略過載具那項，不改設定）。
// 執行：SMOKE_BASE=http://localhost:3000 node scripts/pos-sticky-panel-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "pos-sticky-panel");
mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const VIEWPORT = { width: 1440, height: 900 };
const LINES = 8;

async function inViewport(page, locator) {
  const box = await locator.boundingBox();
  return box !== null && box.y >= 0 && box.y + box.height <= VIEWPORT.height;
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: VIEWPORT });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  await skipOpeningCheckRedirect(page, BASE);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/pos`);
  await page.waitForSelector(".pos-menu-tile");

  const tiles = page.locator(".pos-menu-tile").filter({ hasNotText: /剩|售完/ });
  for (let i = 0; i < LINES; i++) {
    await tiles.nth(i).click();
    const dialog = page.locator('[role="dialog"]');
    await dialog.waitFor();
    await dialog.getByRole("button", { name: "加入購物車" }).click();
    await dialog.waitFor({ state: "detached" });
  }
  await page.waitForSelector(`.pos-cart tbody tr:nth-child(${LINES})`);
  await page.getByRole("radio", { name: "外帶", exact: true }).click();
  await page.screenshot({ path: `${SHOTS}/01-top.png` });

  // 頁首下方還有標題／顧客螢幕列，頁面在最頂端時面板尚未黏住；往下捲一點後才是收銀常態。
  await page.evaluate(() => window.scrollTo(0, 200));
  await page.waitForTimeout(300);
  const panel = page.locator(".pos-right");
  const panelBox = await panel.boundingBox();
  ok(
    "右側面板高度不超出畫面",
    panelBox !== null && panelBox.y + panelBox.height <= VIEWPORT.height,
    panelBox ? `底緣 ${Math.round(panelBox.y + panelBox.height)} / ${VIEWPORT.height}` : "找不到面板",
  );
  const checkout = page.locator(".pos-checkout");
  ok("面板黏住後看得到結帳鈕", await inViewport(page, checkout));

  // 捲到左欄菜單最底：面板與結帳鈕都要還在畫面內
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.waitForTimeout(300);
  ok("頁面捲到最底仍看得到結帳鈕", await inViewport(page, checkout));
  await page.evaluate(() => window.scrollTo(0, 200));
  await page.waitForTimeout(300);

  // 載具欄（電子發票啟用時才有）：在面板內就能捲到，不必捲整頁
  const carrier = page.locator('input[name="inv-carrier"]');
  if ((await carrier.count()) === 0) {
    ok("載具欄（略過：本環境未啟用電子發票）", true);
  } else {
    await panel.hover();
    const before = await page.evaluate(() => window.scrollY);
    await page.mouse.wheel(0, 2000);
    await page.waitForTimeout(300);
    const after = await page.evaluate(() => window.scrollY);
    ok("在面板上滾輪只捲面板、不帶動整頁", after === before, `scrollY ${before} → ${after}`);
    ok("面板內捲到底看得到載具欄", await inViewport(page, carrier));
    ok("面板內捲到底仍看得到結帳鈕", await inViewport(page, checkout));
    await page.screenshot({ path: `${SHOTS}/02-panel-scrolled.png` });
  }

  // 窄螢幕（單欄）時面板回到一般排版，不限高
  await page.setViewportSize({ width: 1000, height: 900 });
  await page.waitForTimeout(300);
  const overflow = await panel.evaluate((el) => getComputedStyle(el).overflowY);
  ok("窄螢幕單欄時面板不另外捲動", overflow === "visible", `overflow-y=${overflow}`);
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
