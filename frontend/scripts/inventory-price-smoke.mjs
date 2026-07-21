// 庫存改售價瀏覽器煙霧測試（限店長、寫稽核）：登入 → /inventory →
// 序號品 / 一般商品 / 散裝批三類各做一次「改價」：開窗 → 防呆（0 元擋下）→ 改成新價 → 送出 →
// 重開同列確認新價已持久（經後端 + 重載驗證），並截圖供操作手冊使用。
// 需 backend:8000 + frontend:3000 已起、lucamp_e2e 已 seed（dev-manager + seed_dev_demo）。
import { mkdirSync } from "node:fs";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/inv-price-shots";
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

// 對某一分頁的「第一列」做改價並驗證持久化。clicks 第一顆「改價」鈕，
// 讀回 prefilled 舊價 → 防呆 0 元 → 改 newPrice → 送出 → 重開同列確認值已變。
async function changeFirstPrice(label, shotTag) {
  // 第一顆改價鈕
  const firstBtn = page.locator('button:has-text("改價")').first();
  await firstBtn.waitFor({ state: "visible", timeout: 8000 });
  await firstBtn.click();
  const dialog = page.locator('[aria-label="改售價"]');
  await dialog.waitFor({ state: "visible", timeout: 4000 });
  const input = dialog.locator('input[aria-label="新售價"]');
  const oldPrice = Number(await input.inputValue());
  ok(`${label}：開啟改價視窗、帶出現價`, Number.isFinite(oldPrice) && oldPrice > 0, `現價=${oldPrice}`);

  // 防呆：0 元應被擋、視窗不關
  await input.fill("0");
  await dialog.locator('button:has-text("送出")').click();
  const errVisible = await dialog
    .locator('text=售價須為正整數元')
    .isVisible()
    .catch(() => false);
  ok(`${label}：0 元被防呆擋下`, errVisible);

  // 截一張「改價視窗 + 防呆」供手冊
  if (shotTag) await page.screenshot({ path: `${SHOTS}/${shotTag}-dialog.png` });

  // 改成新價（與舊價不同、避免千分位逗號 → 取 < 1000 的明確值或舊價+137）
  const newPrice = oldPrice + 137;
  await input.fill(String(newPrice));
  await dialog.locator('button:has-text("送出")').click();
  await dialog.waitFor({ state: "hidden", timeout: 6000 });
  ok(`${label}：送出後視窗關閉`, true);

  // 重開同一列，確認新價已持久（經後端寫入 + 列表重載）
  await page.locator('button:has-text("改價")').first().click();
  const dialog2 = page.locator('[aria-label="改售價"]');
  await dialog2.waitFor({ state: "visible", timeout: 4000 });
  const persisted = Number(await dialog2.locator('input[aria-label="新售價"]').inputValue());
  ok(`${label}：新價已持久`, persisted === newPrice, `期望 ${newPrice}、實得 ${persisted}`);
  await dialog2.locator('button:has-text("取消")').click();
  await dialog2.waitFor({ state: "hidden", timeout: 4000 });
}

async function switchTab(tabLabel) {
  await page.locator(`[role="tab"]:has-text("${tabLabel}")`).click();
  await page.waitForTimeout(600);
}

try {
  // 1) 登入
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(300);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功（店長）", true);

  // 2) 進庫存（預設序號品分頁）
  await page.click('a:has-text("庫存")');
  await page.waitForURL(`${BASE}/inventory`);
  await page.waitForSelector('[role="tab"]:has-text("序號品")');
  await page.waitForTimeout(600);
  ok("庫存頁載入", true);
  await page.screenshot({ path: `${SHOTS}/00-inventory.png` });

  // 3) 序號品改標價（先篩在庫，因預設清單含已售出列、已售不可改價）
  await page.locator("select").first().selectOption("IN_STOCK");
  await page.waitForTimeout(900);
  ok("序號品：篩選在庫", true);
  await changeFirstPrice("序號品（標價）", "01-serialized");

  // 4) 一般商品改單價
  await switchTab("一般商品");
  await changeFirstPrice("一般商品（單價）", "02-catalog");

  // 5) 散裝批改單價
  await switchTab("散裝批");
  await changeFirstPrice("散裝批（單價）", "03-bulk");
} catch (err) {
  ok("流程未完成（例外）", false, String(err));
} finally {
  const passed = results.filter((r) => r.pass).length;
  console.log(`\n${passed}/${results.length} 通過`);
  await browser.close();
  process.exit(passed === results.length ? 0 : 1);
}
