// 錯誤畫面煙霧測試（2026-10-26 LINE Pay 結帳後整頁變黑底「Application error」）：
// 登入 → 叫號頁的清單 API 被攔截成壞資料、畫面渲染當掉 → 顯示自訂錯誤頁（實際錯誤內容、
// 頂欄仍在、深色模式下照樣可讀）→ 放行 API 後按「重新載入」恢復 → 「回到 POS 結帳」整頁導向 /pos。
// 需 backend + frontend 已起、已 seed（dev-manager）。用正式版建置（next start）跑，
// 錯誤頁行為才跟店裡一致（dev 模式會多一層錯誤浮層）。
// 執行：SMOKE_BASE=http://localhost:3100 node frontend/scripts/error-screen-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "error-screen");
mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
// 店裡那台 Mac 是深色模式：Next 預設錯誤頁就是因此變成黑底。
const context = await browser.newContext({
  viewport: { width: 1280, height: 900 },
  colorScheme: "dark",
});
const page = await context.newPage();
await skipOpeningCheckRedirect(page);

// 叫號清單 API 回傳「不是陣列」的壞資料 → rows.map 丟 TypeError → 錯誤邊界接手。
// 帶 length 才會過畫面上的 rows.length > 0 判斷、走到 map（純物件會被當成空清單）。
let breakTickets = true;
await page.route(/\/api\/v1\/call-tickets(\?|$)/, async (route) => {
  if (!breakTickets) return route.continue();
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ length: 1 }),
  });
});

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  await page.goto(`${BASE}/call-tickets`, { waitUntil: "networkidle" });
  await page.waitForSelector(".error-screen", { timeout: 10000 });
  const text = (await page.locator(".error-screen").textContent()) ?? "";
  ok("顯示自訂錯誤頁（不是 Next 預設黑底頁）", text.includes("畫面發生錯誤"));
  ok("錯誤頁列出實際錯誤", text.includes("TypeError") && text.includes("map"), text.slice(0, 160));
  ok("錯誤頁列出頁面路徑", text.includes("/call-tickets"));
  ok(
    "Next 預設的 Application error 沒出現",
    !(await page.content()).includes("Application error: a client-side exception"),
  );
  ok("頂欄導覽仍在", (await page.locator('a:has-text("POS 結帳")').count()) > 0);
  await page.screenshot({ path: join(SHOTS, "1-error-screen-dark.png"), fullPage: true });

  breakTickets = false;
  await page.click('button:has-text("重新載入")');
  await page.waitForLoadState("networkidle");
  await page.waitForSelector("h1:has-text(\"叫號\")", { timeout: 10000 });
  ok("放行 API 後按「重新載入」恢復正常頁面", (await page.locator(".error-screen").count()) === 0);
  await page.screenshot({ path: join(SHOTS, "2-after-retry.png"), fullPage: true });

  breakTickets = true;
  await page.goto(`${BASE}/call-tickets`, { waitUntil: "networkidle" });
  await page.waitForSelector(".error-screen", { timeout: 10000 });
  await page.click('.error-screen a:has-text("回到 POS 結帳")');
  await page.waitForURL(`${BASE}/pos`);
  ok("「回到 POS 結帳」導向 /pos", true);
  await page.screenshot({ path: join(SHOTS, "3-back-to-pos.png"), fullPage: true });
} catch (err) {
  ok("流程未中斷", false, String(err));
  await page.screenshot({ path: join(SHOTS, "failure.png"), fullPage: true });
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過；截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
