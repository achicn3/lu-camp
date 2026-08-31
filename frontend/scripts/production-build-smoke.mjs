// 正式版建置煙霧：確認 `next build` + `next start` 的行為與開發模式一致。
//
// 為什麼需要這支：店裡不該跑 `next dev`（每頁即時編譯、常駐編譯器吃記憶體、出錯會把
// 紅色錯誤堆疊蓋在客人看得到的畫面上、檔案監看失效會讓頁面莫名壞掉）。但正式版建置
// 的行為跟開發模式**不完全相同**——只在開發模式被容忍的問題，要在這裡被抓出來。
//
// 刻意只做唯讀導覽：不結帳、不列印、不踢錢櫃。真代理接的是店裡的實機，煙霧測試不該
// 吐紙。
//
// 執行（先 pnpm build && pnpm start）：
//   node frontend/scripts/production-build-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const strip = (u) => u.replace(/\/+$/, "");
const BASE = strip(process.env.SMOKE_BASE ?? "http://localhost:3000");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "production-build");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

// 每頁的「這頁真的長出來了」判準：只看 200 不夠——客戶端渲染失敗時外殼仍會回 200。
const PAGES = [
  ["/", "門市作業"],
  ["/pos", "POS 結帳"],
  ["/sales", "交易紀錄"],
  ["/cash", "現金對帳"],
  ["/contacts", "會員/賣方"],
  ["/acquisition", "收購"],
  ["/call-tickets", "叫號"],
  ["/signing", "簽署紀錄"],
  ["/inventory", "庫存"],
  ["/consignment", "寄售付款"],
  ["/purchasing", "採購 / 補貨"],
  ["/stocktake", "盤點"],
  ["/campaigns", "門市活動"],
  ["/menu", "餐飲菜單"],
  ["/reports", "報表"],
  ["/einvoice-queue", "發票待處理"],
  ["/settings", "設定"],
  ["/backup", "備份"],
];

mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
try {
  const context = await browser.newContext();
  const page = await context.newPage();

  // 開發模式的錯誤覆蓋層在正式版不存在，錯誤只會留在 console——所以一定要收。
  const consoleErrors = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(`${page.url()} :: ${m.text()}`);
  });
  page.on("pageerror", (e) => consoleErrors.push(`${page.url()} :: ${e.message}`));

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("帳號").fill(USERNAME);
  await page.getByLabel("密碼").fill(PASSWORD);
  await page.getByRole("button", { name: "登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  ok("正式版可登入", true);

  for (const [path, heading] of PAGES) {
    const res = await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
    let rendered = true;
    try {
      await page.getByRole("heading", { name: heading }).first().waitFor({ timeout: 15000 });
    } catch {
      rendered = false;
    }
    ok(`${path} 內容渲染（${heading}）`, res?.status() === 200 && rendered, `HTTP ${res?.status()}`);
    await page.screenshot({ path: join(SHOTS, `${path.replace(/\W+/g, "_") || "_home"}.png`) });
  }

  // 正式版不該把技術性錯誤留在畫面背後——開發模式的紅色覆蓋層沒了，這些就沒人看得到。
  ok("全程沒有 console 錯誤", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
  await context.close();
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過；截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
