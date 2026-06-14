// POS 結帳瀏覽器煙霧測試（F3 整合驗證）：登入 → /pos → 掃描序號品 → 現金結帳 →
// 完成畫面 + 列印明細對話框 → 列印明細。執行：在 mcr playwright 容器內 node scripts/pos-smoke.mjs
// 需 backend:8000 + frontend:3000 已起、lucamp 已 seed（dev-manager、TENT-001 在庫、已開帳）。
import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/pos-shots";
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

  // 2) 進 POS
  await page.click('a:has-text("POS 結帳")');
  await page.waitForURL(`${BASE}/pos`);
  await page.waitForSelector("text=本期不開票");
  ok("POS 載入＋發票區隱藏（本期不開票）", true);
  ok("空車提示", await page.locator("text=掃描或輸入商品條碼開始結帳").isVisible());
  ok("空車時結帳鍵停用", await page.locator('button:has-text("結帳")').isDisabled());
  await page.screenshot({ path: `${SHOTS}/01-pos-empty.png` });

  // 3) 掃描序號品
  await page.fill('input[name="code"]', "TENT-001");
  await page.press('input[name="code"]', "Enter");
  await page.waitForSelector("text=雙人帳篷(測試)");
  ok("掃描序號品加入購物車", true);
  const total = await page.locator(".pos-total strong").textContent();
  ok("應付總額 = 1,800", total?.includes("1,800") ?? false, total ?? "");
  await page.screenshot({ path: `${SHOTS}/02-pos-cart.png` });

  // 4) 切購物金（無會員）→ 應顯示需指定買方、結帳鍵停用
  await page.locator(".pos-tender-mode", { hasText: "購物金" }).click();
  await page.waitForSelector("text=以購物金付款必須先指定買方會員");
  ok("購物金無會員 → 阻擋並提示", await page.locator('button:has-text("結帳")').isDisabled());
  await page.locator(".pos-tender-mode", { hasText: "現金" }).click(); // 切回現金

  // 5) 現金結帳
  await page.click('button:has-text("結帳")');
  await page.waitForSelector("text=已完成");
  ok("現金結帳完成", true);
  ok("列印明細對話框跳出", await page.locator('[role="dialog"]:has-text("列印商品明細？")').isVisible());
  await page.screenshot({ path: `${SHOTS}/03-pos-complete.png` });

  // 6) 列印明細
  await page.click('[role="dialog"] button:has-text("列印明細")');
  await page.waitForSelector("text=已送出列印");
  ok("列印明細送出（稽核）", true);
  await page.click('[role="dialog"] button:has-text("完成")');

  // 7) 下一筆 → 回空車
  await page.click('button:has-text("開始下一筆")');
  await page.waitForSelector("text=掃描或輸入商品條碼開始結帳");
  ok("開始下一筆回到空車", true);
} catch (error) {
  ok("流程中斷", false, String(error));
  await page.screenshot({ path: `${SHOTS}/99-failure.png` });
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n結果：${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length ? 1 : 0);
