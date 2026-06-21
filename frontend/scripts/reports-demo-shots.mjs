// 財務報表示範截圖：登入 → /reports → 截「今日營運」「趨勢」「銷售毛利」「庫存價值」分頁。
import { mkdirSync } from "node:fs";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-shots/reports-demo";
const USER = process.env.SEED_USER ?? "dev-manager";
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1366, height: 1000 } });
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USER);
  await page.fill('input[name="password"]', PASS);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });

  async function shot(tabLabel, file) {
    if (tabLabel) {
      await page.click(`button:has-text("${tabLabel}")`);
      await page.waitForTimeout(1200); // 等查詢與（趨勢）圖渲染
    }
    await page.screenshot({ path: `${SHOTS}/${file}`, fullPage: true });
    console.log(`📸 ${file}`);
  }

  await page.waitForTimeout(1200);
  await shot(null, "01-today.png"); // 預設「今日營運」
  await shot("趨勢", "02-trends.png");
  await shot("銷售毛利", "03-sales-margin.png");
  await shot("活動成效", "04-campaign-performance.png");
  await shot("庫存價值", "05-inventory-value.png");
  console.log(`截圖：${SHOTS}`);
} finally {
  await browser.close();
}
