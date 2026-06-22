// B3 標籤列印瀏覽器煙霧測試：登入 → /acquisition → 買斷一件 → 收購完成卡片出現
// 「列印標籤（N 張）」按鈕 → 點擊 → 經硬體代理（:8001 /print/label）→ 顯示「已送出 N 張標籤」。
// 需 backend(:8000) + frontend(:3000) + hardware-agent(:8001) 已起、已 seed（dev-manager + 開帳）。
// 代理可用 Fake 標籤機（驗 UI 流程）；要真打 Brother 需 AGENT_DEVICES=real + AGENT_BROTHER_HOST。
// 執行：mcr playwright 容器內 node scripts/label-print-smoke.mjs
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

  // 2) 進收購頁 → 建賣方 → 買斷一件（現金，已開帳）
  await page.click('a:has-text("收購")');
  await page.waitForURL(`${BASE}/acquisition`);
  await page.waitForSelector('[role="tab"]:has-text("買斷")');
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', "標籤測試賣家");
  await page.fill('input[aria-label="身分證字號"]', "A123456789");
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector("text=標籤測試賣家");

  await page.fill('input[aria-label="品名"]', "標籤測試外套");
  await page.locator(".acq-row select").first().selectOption("A");
  await page.fill('input[aria-label="收購價"]', "1000");
  await page.fill('input[aria-label="上架售價"]', "3000");
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成");
  ok("買斷送出完成（有序號條碼）", await page.locator("text=序號條碼").isVisible());

  // 3) 列印標籤按鈕出現（張數 = 序號品數）
  const labelBtn = page.locator('.acq-print-labels button:has-text("列印標籤")');
  await labelBtn.waitFor();
  ok("出現「列印標籤（N 張）」按鈕", true, (await labelBtn.textContent()) ?? "");
  await page.screenshot({ path: `${SHOTS}/b3-01-label-button.png` });

  // 4) 點擊 → 經代理列印 → 顯示「已送出 N 張標籤」（代理需在 :8001 回應）
  await labelBtn.click();
  await page.waitForSelector(".acq-print-labels .form-success, .acq-print-labels .form-error", {
    timeout: 15000,
  });
  const sent = await page.locator(".acq-print-labels .form-success").count();
  if (sent > 0) {
    ok(
      "標籤列印送出成功",
      true,
      (await page.locator(".acq-print-labels .form-success").textContent()) ?? "",
    );
  } else {
    ok(
      "標籤列印（代理回應）",
      false,
      (await page.locator(".acq-print-labels .form-error").textContent()) ?? "",
    );
  }
  await page.screenshot({ path: `${SHOTS}/b3-02-label-printed.png` });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
