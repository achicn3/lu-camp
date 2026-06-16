// F4 會員中心瀏覽器煙霧測試＋截圖（docs/10 §8.1）。
// 需：backend、frontend（production build）已起、dev 帳號＋示範會員已 seed。
// 截圖輸出 SMOKE_SHOTS（預設 ~/tmp/lu-camp-shots）。
import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/shots";
const results = [];
const ok = (name, pass, detail = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  // 登入
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  // 會員清單
  await page.click('a:has-text("會員/賣方")');
  await page.waitForURL(`${BASE}/contacts`);
  await page.waitForSelector(".member-row");
  ok("會員清單顯示示範會員", await page.locator("text=王示範").first().isVisible());
  await page.screenshot({ path: `${SHOTS}/f4-01-member-list.png`, fullPage: true });

  // 進入詳情 → 總覽
  await page.click('a.member-row:has-text("王示範")');
  await page.waitForSelector(".member-tabs");
  await page.waitForSelector("text=購物金餘額");
  ok("總覽顯示購物金餘額", await page.locator("text=$1,500").first().isVisible());
  ok("總覽顯示寄售待撥", await page.locator("text=$800").first().isVisible());
  await page.screenshot({ path: `${SHOTS}/f4-02-overview.png`, fullPage: true });

  // 消費紀錄 + 展開明細
  await page.click('.member-tab:has-text("消費紀錄")');
  await page.waitForSelector(".data-table");
  await page.click('button:has-text("明細")');
  await page.waitForSelector(".member-subpanel");
  ok("消費明細展開", await page.locator(".member-subpanel:has-text(\"明細\")").isVisible());
  await page.screenshot({ path: `${SHOTS}/f4-03-purchases.png`, fullPage: true });

  // 寄售
  await page.click('.member-tab:has-text("寄售")');
  await page.waitForSelector(".member-banner");
  ok("寄售待撥加總顯示", await page.locator(".member-banner:has-text(\"800\")").isVisible());
  await page.screenshot({ path: `${SHOTS}/f4-04-consignments.png`, fullPage: true });

  // 帶來的商品（買斷∪寄售）
  await page.click('.member-tab:has-text("帶來的商品")');
  await page.waitForSelector(".data-table");
  ok("商品來源含買斷與寄售", await page.locator("text=買斷").first().isVisible());
  await page.screenshot({ path: `${SHOTS}/f4-05-sourced.png`, fullPage: true });

  // 編輯（MANAGER：角色＋身分證區）
  await page.click('.member-tab:has-text("編輯")');
  await page.waitForSelector('input[name="name"]');
  ok("MANAGER 看得到角色/身分證區", await page.locator("text=角色與身分證").isVisible());
  await page.screenshot({ path: `${SHOTS}/f4-06-edit.png`, fullPage: true });
} catch (error) {
  ok("流程中斷", false, String(error));
  await page.screenshot({ path: `${SHOTS}/f4-99-failure.png`, fullPage: true });
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n結果：${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length ? 1 : 0);
