// 庫存瀏覽器煙霧測試（F5 整合驗證）：登入 → /inventory → 三分頁清單/篩選/低庫存/售出進度。
// 執行：mcr playwright 容器內 node scripts/inventory-smoke.mjs
// 需 backend:8000 + frontend:3000 已起、lucamp_f5 已 seed（dev-manager + SER-* / SKU-* / LOT-1）。
import { mkdirSync } from "node:fs";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/inv-shots";
// 確保截圖目錄存在（新鮮容器內預設路徑可能不存在；Codex P2）。
mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  // 1) 登入
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  // 2) 進庫存
  await page.click('a:has-text("庫存")');
  await page.waitForURL(`${BASE}/inventory`);
  await page.waitForSelector("text=SER-001");
  ok("序號品分頁載入＋列出", await page.locator("text=SER-002").isVisible());
  ok("含已售出列（未篩選）", await page.locator("text=SER-003").isVisible());
  ok("持有 badge=自有", await page.locator('.inv-badge:has-text("自有")').first().isVisible());
  await page.screenshot({ path: `${SHOTS}/01-serialized.png` });

  // 3) 序號品狀態篩選：IN_STOCK → 已售出列消失（重抓期間列表會短暫清空，故用 auto-wait）
  await page.selectOption('select[aria-label="狀態"]', "IN_STOCK");
  await page.waitForSelector("text=SER-003", { state: "detached" });
  await page.waitForSelector("text=SER-001"); // auto-wait 重抓後保留列
  ok("狀態篩選 IN_STOCK 縮小結果（SER-003 消失、SER-001 留存）", true);

  // 4) 數量品分頁：低庫存 badge + 篩選
  await page.click('button[role="tab"]:has-text("數量品")');
  await page.waitForSelector("text=SKU-1");
  ok("數量品列出", await page.locator("text=SKU-2").isVisible());
  ok("低庫存 badge（SKU-1，2≤5）", await page.locator('.inv-badge:has-text("低庫存")').isVisible());
  await page.check('.inv-check input[type="checkbox"]');
  await page.waitForSelector("text=SKU-2", { state: "detached" });
  await page.waitForSelector("text=SKU-1"); // auto-wait 重抓後保留列
  ok("僅顯示低庫存 → SKU-2 消失、SKU-1 留存", true);
  await page.screenshot({ path: `${SHOTS}/02-catalog-lowstock.png` });

  // 5) 散裝批分頁：售出進度
  await page.click('button[role="tab"]:has-text("散裝批")');
  await page.waitForSelector("text=LOT-1");
  ok("散裝批列出", true);
  ok("售出進度 60%（(10-4)/10）", await page.locator("text=60%").isVisible());
  ok("狀態 badge=販售中", await page.locator('.inv-badge:has-text("販售中")').isVisible());
  await page.screenshot({ path: `${SHOTS}/03-bulk.png` });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
