// 收購瀏覽器煙霧測試（F6＋F6.5 整合驗證）：登入 → /acquisition → 建立賣方 → 買斷一件
// （品牌/分類查無即建、定價輔助、現金收購）→ 序號條碼 →（F6.5 管理者）作廢這筆收購 → 以單號查詢確認已作廢。
// 需 backend + frontend 已起、已 seed（dev-manager + 開帳）。執行：mcr playwright 容器內 node scripts/acquisition-smoke.mjs
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
  // 1) 登入
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  // 2) 進收購頁
  await page.click('a:has-text("收購")');
  await page.waitForURL(`${BASE}/acquisition`);
  await page.waitForSelector('[role="tab"]:has-text("買斷")');
  ok("收購頁載入＋中文分頁", await page.locator('[role="tab"]:has-text("寄售")').isVisible());

  // 3) 建立賣方
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', "王賣家");
  await page.fill('input[aria-label="身分證字號"]', "A123456789");
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector("text=王賣家");
  ok("建立並選取賣方", true);

  // 4) 鑑價列：品名、成色、品牌（建）、分類（建，seed 規則）
  await page.fill('input[aria-label="品名"]', "登山外套");
  await page.locator(".acq-row select").first().selectOption("A");

  const brand = page.getByLabel("品牌");
  await brand.click();
  await brand.fill("TestBrand");
  await page.click('button:has-text("建立「TestBrand」")');
  ok("品牌查無即建", true);

  const cat = page.getByLabel("分類");
  await cat.click();
  await cat.fill("登山服飾");
  await page.click('button:has-text("建立「登山服飾」")');
  ok("分類查無即建（seed 定價規則）", true);

  // 5) 估計轉售價 → 建議最高收購成本（雙重約束定價輔助）
  await page.fill('input[aria-label="估計轉售價"]', "3000");
  await page.waitForSelector("text=建議最高收購成本");
  ok("顯示建議最高收購成本", true);
  await page.screenshot({ path: `${SHOTS}/01-buyout-aid.png` });

  // 6) 收購價 + 上架售價 → 現金送出（已開帳）
  await page.fill('input[aria-label="收購價"]', "1000");
  await page.fill('input[aria-label="上架售價"]', "3000");
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成");
  ok("現金收購送出完成", true);
  ok("顯示序號條碼", await page.locator("text=序號條碼").isVisible());
  await page.screenshot({ path: `${SHOTS}/02-buyout-done.png` });

  // 7) F6.5：管理者作廢剛建立的收購（這筆有誤？作廢收購 → 填原因 → 二次確認）
  const orderText = (await page.locator(".acq-result").textContent()) ?? "";
  const orderId = orderText.match(/#(\d+)/)?.[1] ?? "";
  ok("取得收購單號", orderId !== "", `#${orderId}`);
  await page.click('button:has-text("這筆有誤？作廢收購")');
  await page.waitForSelector('[role="dialog"][aria-label="作廢收購確認"]');
  ok("作廢確認對話框跳出", true);
  await page.fill('textarea[aria-label="作廢原因"]', "煙霧測試誤建，作廢");
  await page.click('[role="dialog"] button:has-text("確認作廢")');
  await page.waitForSelector("text=已作廢收購單");
  ok("作廢完成並顯示反轉摘要", true);
  await page.screenshot({ path: `${SHOTS}/03-void-done.png` });

  // 8) F6.5：以單號查詢確認已作廢、且不再出現作廢入口
  await page.fill('input[aria-label="收購單號"]', orderId);
  await page.click('button:has-text("查詢")');
  await page.waitForSelector("text=作廢時間");
  ok("查詢顯示已作廢（作廢時間）", true);
  ok(
    "已作廢單不再出現作廢入口",
    (await page.locator('.acq-void-section button:has-text("作廢收購")').count()) === 0,
  );
  await page.screenshot({ path: `${SHOTS}/04-void-lookup.png` });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
