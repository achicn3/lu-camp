// 商品備註瀏覽器煙霧測試（2026-09-02 裁示：單一備註欄位，POS 結帳跳提醒）：
// 登入 → /inventory → 序號品明細寫備註 → 回列表確認摘要出現 →
// /pos 掃該商品 → 購物車行內顯示備註 → 按結帳 → 跳出提醒對話框 →
// 「回購物車」不成交 → 再按結帳 → 「已確認，繼續結帳」→ 成交。
// 需 backend:8000 + frontend:3000 已起、lucamp_e2e 已 seed（dev-manager + seed_dev_demo）。
// 流程最後會把那件商品賣掉 → **每次重跑前要重建 lucamp_e2e 並重新 seed**（docs/20 §1-2）。
import { mkdirSync } from "node:fs";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/inv-note-shots";
mkdirSync(SHOTS, { recursive: true });

const NOTE = "缺營釘一支，交貨前要跟客人說";

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
  await page.waitForTimeout(300);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功（店長）", true);

  // 2) 庫存頁 → 序號品「在庫」第一列
  await page.click('a:has-text("庫存")');
  await page.waitForURL(`${BASE}/inventory`);
  await page.waitForSelector('[role="tab"]:has-text("序號品")');
  await page.locator("select").first().selectOption("IN_STOCK");
  await page.waitForTimeout(900);
  const rowCount = await page.locator("table tbody tr").count();
  if (rowCount === 0) {
    // 本腳本最後會把那件商品賣掉，故需要新鮮的 seed 才能重跑（見 docs/20 §2）。
    throw new Error("沒有在庫序號品可測——請先重建 lucamp_e2e 並重跑 seed_dev_demo");
  }
  const firstRow = page.locator("table tbody tr").first();
  const itemCode = (await firstRow.locator("td").first().innerText()).trim();
  ok("取得在庫序號品", itemCode.length > 0, itemCode);

  // 3) 開明細 → 寫備註
  await firstRow.locator('button:has-text("詳細")').click();
  const detail = page.locator('[aria-label="商品明細"]');
  await detail.waitFor({ state: "visible", timeout: 6000 });
  await detail.locator('button:has-text("新增備註"), button:has-text("編輯備註")').first().click();
  const textarea = detail.locator('textarea[aria-label="商品備註"]');
  await textarea.waitFor({ state: "visible", timeout: 4000 });
  await textarea.fill(NOTE);
  await page.screenshot({ path: `${SHOTS}/01-note-edit.png` });
  await detail.locator('button:has-text("儲存備註")').click();
  await detail.locator(`text=${NOTE}`).waitFor({ state: "visible", timeout: 6000 });
  ok("明細內寫入備註並顯示", true);
  await page.screenshot({ path: `${SHOTS}/02-note-saved.png` });
  await detail.locator('button:has-text("關閉")').click();
  await detail.waitFor({ state: "hidden", timeout: 4000 });

  // 4) 列表摘要（內部備忘要在列表就看得到，不必逐件點開）
  await page.reload({ waitUntil: "networkidle" });
  await page.locator("select").first().selectOption("IN_STOCK");
  await page.waitForTimeout(900);
  const summaryVisible = await page
    .locator('.inv-note-summary')
    .first()
    .isVisible()
    .catch(() => false);
  ok("列表顯示備註摘要", summaryVisible);
  await page.screenshot({ path: `${SHOTS}/03-list-summary.png` });

  // 5) 現金結帳需在開帳中（§7.8）：先確保有開帳的錢櫃 session
  await page.click('a:has-text("現金對帳")');
  await page.waitForURL(`${BASE}/cash`);
  await page.waitForSelector('input[name="opening_float"], .badge-open', { timeout: 8000 });
  if (await page.locator('input[name="opening_float"]').count()) {
    await page.fill('input[name="opening_float"]', "3000");
    await page.click('button:has-text("開帳")');
    await page.waitForSelector(".badge-open", { timeout: 8000 });
    ok("開帳成功（零用金 3,000）", true);
  } else {
    ok("已在開帳中", true);
  }

  // 6) POS 掃該商品 → 行內顯示備註
  await page.click('a:has-text("POS 結帳")');
  await page.waitForURL(`${BASE}/pos`);
  await page.waitForSelector('input[name="code"]', { timeout: 15000 });
  await page.fill('input[name="code"]', itemCode);
  await page.press('input[name="code"]', "Enter");
  const lineNote = page.locator(".pos-line-note").first();
  await lineNote.waitFor({ state: "visible", timeout: 8000 });
  ok("POS 購物車行內顯示備註", true);
  await page.screenshot({ path: `${SHOTS}/04-pos-cart-note.png` });

  // 6) 按結帳 → 跳提醒（尚未成交）
  await page.locator("button.pos-checkout").click();
  const dialog = page.locator('[aria-label="商品備註提醒"]');
  await dialog.waitFor({ state: "visible", timeout: 8000 });
  const dialogHasNote = await dialog.locator(`text=${NOTE}`).isVisible().catch(() => false);
  ok("結帳跳出備註提醒且內容正確", dialogHasNote);
  await page.screenshot({ path: `${SHOTS}/05-checkout-reminder.png` });

  // 7)「回購物車」→ 對話框關閉、未成交
  await dialog.locator('button:has-text("回購物車")').click();
  await dialog.waitFor({ state: "hidden", timeout: 4000 });
  const stillInCart = await page.locator(".pos-line-note").first().isVisible().catch(() => false);
  ok("回購物車後未成交、商品仍在車上", stillInCart);

  // 8) 再按結帳 → 仍會提醒（沒確認過就不放行）→ 確認後成交
  await page.locator("button.pos-checkout").click();
  await dialog.waitFor({ state: "visible", timeout: 8000 });
  ok("未確認過的備註會再次提醒", true);
  await dialog.locator('button:has-text("已確認，繼續結帳")').click();
  await page.locator("text=已完成").first().waitFor({ state: "visible", timeout: 15000 });
  ok("確認後完成結帳", true);
  await page.screenshot({ path: `${SHOTS}/06-checkout-done.png` });
} catch (err) {
  ok("流程未完成（例外）", false, String(err));
} finally {
  const passed = results.filter((r) => r.pass).length;
  console.log(`\n${passed}/${results.length} 通過`);
  await browser.close();
  process.exit(passed === results.length ? 0 : 1);
}
