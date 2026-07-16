// 簽署紀錄調閱頁煙霧（docs/29 波次一）：真 backend＋真資料——清單載入、類型過濾、
// 開啟證據（內容快照＋簽名影像實際渲染）、分頁。裁示：調閱不寫稽核。
// 需 SMOKE_BASE（前端）與 SMOKE_API_BASE（後端）；dev-manager 已 seed。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "signing");
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";

let passed = 0;
let failed = 0;
function ok(name, cond, detail = "") {
  if (cond) {
    passed += 1;
    console.log(`✅ ${name}${detail ? `：${detail}` : ""}`);
  } else {
    failed += 1;
    console.log(`❌ ${name}${detail ? `：${detail}` : ""}`);
  }
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 1000 } });
mkdirSync(SHOTS, { recursive: true });

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(700); // 等 hydration，避免原生表單 GET 送出
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', PASS);
  await page.click('button:has-text("登入")');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });

  await page.goto(`${BASE}/signing`, { waitUntil: "networkidle" });
  await page.waitForSelector("table tbody tr", { timeout: 15000 });
  const rowCount = await page.locator("table tbody tr").count();
  ok("簽署紀錄清單載入（預設已簽署）", rowCount > 0, `rows=${rowCount}`);
  await page.screenshot({ path: join(SHOTS, "01-signing-list.png"), fullPage: true });

  // 類型過濾：購物金扣抵確認
  await page.selectOption("select >> nth=1", "STORE_CREDIT_USE");
  await page.waitForTimeout(800);
  const scuRows = await page.locator("table tbody tr").count();
  const kinds = await page.locator("table tbody tr td:nth-child(2)").allInnerTexts();
  ok(
    "類型過濾只剩購物金扣抵確認",
    scuRows > 0 && kinds.every((k) => k === "購物金扣抵確認"),
    `rows=${scuRows}`,
  );
  await page.screenshot({ path: join(SHOTS, "02-filter-scu.png"), fullPage: true });

  // 回到全部類型、開第一筆證據
  await page.selectOption("select >> nth=1", "");
  await page.waitForTimeout(800);
  await page.locator('table tbody tr >> nth=0 >> button:has-text("查看")').click();
  await page.waitForSelector('[role="dialog"]', { timeout: 8000 });
  const dialog = page.locator('[role="dialog"]');
  const hasSnapshot = await dialog.locator("text=簽署當下內容快照").count();
  ok("證據對話框含內容快照", hasSnapshot > 0);
  const img = dialog.locator("img");
  await img.waitFor({ timeout: 10000 });
  const naturalWidth = await img.evaluate((el) => el.naturalWidth);
  ok("簽名影像實際渲染（blob 取回）", naturalWidth > 0, `naturalWidth=${naturalWidth}`);
  await page.screenshot({ path: join(SHOTS, "03-evidence-dialog.png"), fullPage: true });
  await dialog.locator('button:has-text("關閉")').click();

  // 分頁
  await page.locator('button:has-text("下一頁")').click();
  await page.waitForTimeout(800);
  const page2Rows = await page.locator("table tbody tr").count();
  ok("分頁可翻頁", page2Rows > 0, `page2 rows=${page2Rows}`);
  await page.screenshot({ path: join(SHOTS, "04-page-2.png"), fullPage: true });
} catch (e) {
  failed += 1;
  console.log(`❌ 煙霧例外：${String(e).slice(0, 300)}`);
} finally {
  await browser.close();
}

console.log(`\n${passed}/${passed + failed} 通過`);
process.exit(failed === 0 ? 0 : 1);
