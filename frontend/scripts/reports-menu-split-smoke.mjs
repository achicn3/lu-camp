// M2 報表餐飲/二手分列瀏覽器煙霧：登入(MANAGER) → /reports 儀表板顯示「餐飲營收」「二手營收」
// → 切「銷售毛利」分頁亦顯示分列。需 backend(:8000)+frontend(:3000) 已起、有銷售資料（含餐飲）。
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

  await page.click('a:has-text("報表")');
  await page.waitForURL(`${BASE}/reports`);
  await page.waitForSelector(".rpt-dashboard-cards");
  ok("儀表板：餐飲營收卡", (await page.locator("dt:has-text('餐飲營收')").count()) > 0);
  ok("儀表板：二手營收卡", (await page.locator("dt:has-text('二手營收')").count()) > 0);
  await page.screenshot({ path: `${SHOTS}/m2-01-dashboard-split.png` });

  await page.click('[role="tab"]:has-text("銷售毛利")');
  await page.waitForSelector(".inv-table");
  ok(
    "銷售毛利分頁：餐飲/二手分列",
    (await page.locator("td:has-text('餐飲營收')").count()) > 0 &&
      (await page.locator("td:has-text('二手營收')").count()) > 0,
  );
  await page.screenshot({ path: `${SHOTS}/m2-02-sales-margin-split.png` });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
