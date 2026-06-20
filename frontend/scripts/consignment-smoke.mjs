// 寄售付款瀏覽器煙霧測試（Phase 4 / 4A-2）：登入 → /consignment 待付款清單 →
// 二次確認對話框 → 確認付款（現金出帳）→ 已付款分頁驗證。
// 需 backend + frontend 已起、已 seed（dev-manager + 開帳 + 3 筆 PENDING 結算）。
// 執行：node scripts/consignment-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "consignment");
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
  // 1) 登入
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(300);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  // 2) 進寄售付款頁
  await page.click('a:has-text("寄售付款")');
  await page.waitForURL(`${BASE}/consignment`);
  await page.waitForSelector("h1:has-text('寄售付款')");
  ok("寄售付款頁載入", true);

  // 3) 待付款清單：開帳中、3 列、合計
  await page.waitForSelector("table.settle-table tbody tr");
  const rowCount = await page.locator("table.settle-table tbody tr").count();
  ok("待付款清單顯示資料", rowCount >= 3, `${rowCount} 列`);
  ok("顯示開帳中", await page.locator(".settle-drawer-open").isVisible());
  ok("顯示待付款合計", await page.locator(".member-banner .money").isVisible());
  await page.screenshot({ path: `${SHOTS}/01-pending-list.png`, fullPage: true });

  // 4) 對第一列點付款 → 二次確認對話框
  await page.locator('table.settle-table tbody tr button:has-text("付款")').first().click();
  await page.waitForSelector('[role="dialog"][aria-label="確認付款"]');
  ok("付款二次確認對話框跳出", true);
  await page.screenshot({ path: `${SHOTS}/02-confirm-dialog.png`, fullPage: true });

  // 5) 確認付款 → 該列離開待付款清單
  await page.click('[role="dialog"] button:has-text("確認付款")');
  await page.waitForSelector('[role="dialog"]', { state: "detached" });
  await page.waitForTimeout(500);
  const afterCount = await page.locator("table.settle-table tbody tr").count();
  ok("付款後待付款列數減少", afterCount === rowCount - 1, `${rowCount} → ${afterCount}`);
  await page.screenshot({ path: `${SHOTS}/03-after-pay.png`, fullPage: true });

  // 6) 切到「已付款」分頁 → 至少一列
  await page.click('.settle-tabs button:has-text("已付款")');
  await page.waitForSelector("table.settle-table tbody tr");
  const paidCount = await page.locator("table.settle-table tbody tr").count();
  ok("已付款分頁顯示已付列", paidCount >= 1, `${paidCount} 列`);
  ok("已付款狀態標章", await page.locator(".settle-paid").first().isVisible());
  await page.screenshot({ path: `${SHOTS}/04-paid-tab.png`, fullPage: true });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
}finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
