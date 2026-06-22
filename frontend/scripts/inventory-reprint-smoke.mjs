// 庫存補印標籤瀏覽器煙霧測試：登入 → /inventory 序號品分頁 → 某列「補印標籤」→ 經硬體代理
// (:8001 /print/label) → 顯示「✓ 已送出」；再切散裝批分頁驗證該頁也有補印鈕。
// 需 backend(:8000) + frontend(:3000) + hardware-agent(:8001) 已起、已 seed（dev-manager），
// 且庫存中有 IN_STOCK 序號品與 ON_SALE 散裝批（可先跑 acquisition / seed 灌料）。
// 執行：mcr playwright 容器內 node scripts/inventory-reprint-smoke.mjs
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

  // 2) 進庫存頁（預設序號品分頁）
  await page.click('a:has-text("庫存")');
  await page.waitForURL(`${BASE}/inventory`);
  await page.waitForSelector('[role="tab"]:has-text("序號品")');
  const firstReprint = page.locator('.inv-table tbody tr .inv-reprint-btn').first();
  await firstReprint.waitFor();
  ok("序號品列有「補印標籤」鈕", true);
  await page.screenshot({ path: `${SHOTS}/inv-reprint-01-serialized.png` });

  // 3) 點擊補印 → 經代理 → 顯示「✓ 已送出」
  await firstReprint.click();
  await page.waitForSelector(".inv-reprint-ok, .inv-reprint-err", { timeout: 15000 });
  const okCount = await page.locator(".inv-reprint-ok").count();
  ok(
    okCount > 0 ? "序號品補印送出成功" : "序號品補印（代理回應）",
    okCount > 0,
    okCount > 0 ? "✓ 已送出" : ((await page.locator(".inv-reprint-err").getAttribute("title")) ?? ""),
  );
  await page.screenshot({ path: `${SHOTS}/inv-reprint-02-sent.png` });

  // 4) 散裝批分頁也有補印鈕
  await page.click('[role="tab"]:has-text("散裝批")');
  await page.waitForSelector('.inv-table tbody tr');
  const bulkReprint = page.locator('.inv-table tbody tr .inv-reprint-btn');
  ok("散裝批列有「補印標籤」鈕", (await bulkReprint.count()) > 0);
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
