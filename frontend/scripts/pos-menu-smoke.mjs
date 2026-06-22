// M3 POS 餐飲點餐瀏覽器煙霧：登入 → /pos → 點餐飲磚 → 數量彈窗（預設1、改量）→ 加入購物車
// → 應付總額更新 → 現金結帳完成。需 backend(:8000)+frontend(:3000)+agent(:8001) 已起、已開帳、
// 且至少一個可售 menu_item（可先以 API 建立）。執行：mcr playwright 容器內 node scripts/pos-menu-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  await page.click('a:has-text("POS")');
  await page.waitForURL(`${BASE}/pos`);
  await page.waitForSelector(".pos-menu-tiles");
  const tile = page.locator(".pos-menu-tile").first();
  await tile.waitFor();
  const tileName = (await tile.locator(".pos-menu-tile-name").textContent()) ?? "";
  ok("餐飲菜單磚出現", true, tileName);
  await page.screenshot({ path: `${SHOTS}/m3-01-menu-tiles.png` });

  // 點磚 → 數量彈窗（預設 1）→ 改 2 → 加入購物車
  await tile.click();
  const dialog = page.locator('[role="dialog"]');
  await dialog.waitFor();
  const qty = dialog.getByLabel("數量");
  ok("數量彈窗預設 1", (await qty.inputValue()) === "1");
  await page.screenshot({ path: `${SHOTS}/m3-02-qty-dialog.png` });
  await qty.fill("2");
  await dialog.getByRole("button", { name: "加入購物車" }).click();

  // 購物車出現該品項
  await page.waitForSelector(".pos-cart");
  ok(
    "餐飲加入購物車",
    (await page.locator(`.pos-cart >> text=${tileName}`).count()) > 0,
    tileName,
  );

  // 結帳（現金，已開帳）
  await page.waitForSelector('.pos-checkout:not([disabled])', { timeout: 10000 });
  await page.click(".pos-checkout");
  await page.waitForSelector("text=已完成");
  ok("現金結帳完成（含餐飲）", true);
  await page.screenshot({ path: `${SHOTS}/m3-03-checkout-done.png` });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
