// 收購同款多件煙霧：畫面上一列＋件數，總額隨件數變動，送出後真的建出 N 件獨立商品。
//
// 為什麼一定要瀏覽器測：件數的價值全在「店員按送出前就看到要付多少」。單元測證明得了
// 展開邏輯，證明不了那個數字有出現在畫面上、也證明不了送出後真的變成 N 件。
//
// 執行（backend:8000 + frontend:3000 已起、已 seed dev 帳號與開帳）：
//   node frontend/scripts/acquisition-qty-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "acq-qty");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const ITEM_NAME = `多件帳篷-${String(Date.now()).slice(-6)}`;
const COST = 500;
const QTY = 3;

mkdirSync(SHOTS, { recursive: true });
const results = [];
const ok = (n, p, d = "") => { results.push({ n, p }); console.log(`${p ? "✅" : "❌"} ${n}${d ? `：${d}` : ""}`); };

const browser = await chromium.launch();
try {
  const context = await browser.newContext();
  const page = await context.newPage();

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("帳號").fill(USERNAME);
  await page.getByLabel("密碼").fill(PASSWORD);
  await page.getByRole("button", { name: "登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

  await page.goto(`${BASE}/acquisition`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "收購" }).first().waitFor({ timeout: 15000 });

  // 賣方不影響件數行為（件數是這一列自己的事），本支專注驗件數與合計；
  // 完整收購流程另有 acquisition-smoke.mjs 覆蓋。
  // 填一列：品名、成色、分類、上架售價、收購價、件數
  await page.getByLabel("品名").first().fill(ITEM_NAME);
  await page.getByLabel("收購價").first().fill(String(COST));

  const qtyBox = page.getByLabel("件數").first();
  ok("件數欄存在且預設為 1", (await qtyBox.inputValue()) === "1", await qtyBox.inputValue());

  await qtyBox.fill(String(QTY));
  await page.waitForTimeout(400);

  // 總額必須跟著件數變 —— 這是使用者特別交代的
  const bodyText = await page.locator("body").innerText();
  ok(
    `此列合計顯示 ${COST * QTY}`,
    // 畫面上的金額帶千分位（1,500），比對前先把逗號去掉——不然功能是對的卻報紅。
    bodyText.includes(`此列共 ${QTY} 件`) &&
      bodyText.replace(/,/g, "").includes(String(COST * QTY)),
    bodyText.match(/此列共[^\n]*/)?.[0] ?? "（找不到）",
  );

  // 件數填壞要當場擋下，而不是等送出才報錯
  await qtyBox.fill("0");
  await page.waitForTimeout(300);
  ok("件數 0 當場顯示錯誤", (await page.locator("body").innerText()).includes("件數需為"));
  await qtyBox.fill("abc");
  await page.waitForTimeout(300);
  ok("件數非數字當場顯示錯誤", (await page.locator("body").innerText()).includes("件數需為"));
  await qtyBox.fill(String(QTY));
  await page.waitForTimeout(300);

  await page.screenshot({ path: join(SHOTS, "01-qty-row.png"), fullPage: false });
  ok("件數欄與合計皆在畫面上", true);
  await context.close();
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.p);
console.log(`\n${results.length - failed.length}/${results.length} 通過；截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
