// 瀏覽器煙霧測試（開發輔助腳本，非 vitest）：登入＋現金對帳全流程＋排版截圖。
// 執行：node scripts/browser-smoke.mjs（需 backend:8000、frontend:3000、已 seed dev 帳號）
import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-shots";
const results = [];

function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  // 1) 登入頁排版（dev 首次編譯慢：等 networkidle 確保已水合，否則點擊
  // 會觸發原生表單提交、頁面重載）
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SHOTS}/01-login.png` });
  ok("登入頁載入", await page.locator("text=露營二手 POS").isVisible());

  // 2) 錯誤密碼 → inline 錯誤
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "wrong-password");
  await page.click('button:has-text("登入")');
  await page.waitForSelector(".form-error");
  const errText = await page.locator(".form-error").textContent();
  ok("錯誤密碼顯示後端訊息", errText?.includes("帳號或密碼錯誤") ?? false, errText ?? "");
  await page.screenshot({ path: `${SHOTS}/02-login-error.png` });

  // 3) 正確登入 → 首頁
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功導向首頁", true);
  ok("角色徽章顯示管理者", await page.locator("text=管理者").isVisible());
  await page.screenshot({ path: `${SHOTS}/03-home.png` });

  // 4) /cash：開帳
  await page.click('a:has-text("現金對帳")');
  await page.waitForURL(`${BASE}/cash`);
  await page.waitForSelector('label:has-text("開帳零用金")');
  await page.screenshot({ path: `${SHOTS}/04-cash-open-form.png` });
  await page.fill('input[name="opening_float"]', "2000");
  await page.click('button:has-text("開帳")');
  await page.waitForSelector("text=開帳中");
  ok("開帳成功（顯示開帳中＋零用金）", await page.locator("text=2,000").isVisible());
  await page.screenshot({ path: `${SHOTS}/05-cash-open.png` });

  // 5) MANAGER 手動調整（含事由）
  ok("MANAGER 看得到手動調整", await page.locator('label:has-text("調整金額（可負）")').isVisible());
  await page.fill('input[name="amount"]', "-150");
  await page.fill('input[name="note"]', "瀏覽器實測：找錯錢回沖");
  await page.click('button:has-text("送出調整")');
  await page.waitForSelector("text=已調整");
  ok("手動調整成功", true);
  await page.screenshot({ path: `${SHOTS}/06-cash-adjust.png` });

  // 6) 結帳：實點 1900 vs 應有 1850 → 差異 +50
  await page.fill('input[name="counted_amount"]', "1900");
  await page.click('button:has-text("結帳")');
  await page.waitForSelector("text=已結帳");
  const summary = await page.locator(".stat-list").textContent();
  ok(
    "結帳差異呈現（應有 1,850／實點 1,900／差異 50）",
    (summary?.includes("1,850") && summary?.includes("1,900") && summary?.includes("50")) ?? false,
    summary ?? "",
  );
  await page.screenshot({ path: `${SHOTS}/07-cash-closed.png` });

  // 7) 重新開帳 → 回開帳表單（無殘留）
  await page.click('button:has-text("重新開帳")');
  await page.waitForSelector('label:has-text("開帳零用金")');
  ok("重新開帳回到開帳表單", true);

  // 8) CLERK：手動調整應隱藏
  await page.click('button:has-text("登出")');
  await page.waitForURL(`${BASE}/login`);
  await page.fill('input[name="username"]', "dev-clerk");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("CLERK 登入＋徽章", await page.locator("text=店員").isVisible());
  await page.goto(`${BASE}/cash`, { waitUntil: "networkidle" });
  await page.fill('input[name="opening_float"]', "1000");
  await page.click('button:has-text("開帳")');
  await page.waitForSelector("text=開帳中");
  const adjustVisible = await page.locator('label:has-text("調整金額（可負）")').isVisible();
  ok("CLERK 看不到手動調整", !adjustVisible);
  await page.screenshot({ path: `${SHOTS}/08-cash-clerk.png` });
  // 收尾：把 session 關掉以免殘留開帳
  await page.fill('input[name="counted_amount"]', "1000");
  await page.click('button:has-text("結帳")');
  await page.waitForSelector("text=已結帳");

  // 9) 未登入直闖受保護頁 → 導回登入
  await page.click('button:has-text("登出")');
  await page.goto(`${BASE}/cash`);
  await page.waitForURL(`${BASE}/login`);
  ok("未登入訪問 /cash 導回登入", true);
} catch (error) {
  ok("流程中斷", false, String(error));
  await page.screenshot({ path: `${SHOTS}/99-failure.png` });
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n結果：${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length ? 1 : 0);
