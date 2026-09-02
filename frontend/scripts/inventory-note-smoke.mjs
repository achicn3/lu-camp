// 商品備註瀏覽器煙霧測試（2026-09-02 裁示：單一備註欄位，POS 結帳跳提醒）：
// 登入 → /inventory → **序號品／一般商品／散裝批三種型態各寫一筆備註**（三張表一致）→
// 各自列表確認摘要出現 → /pos 三件全掃入 → 購物車三行都顯示備註 → 按結帳 →
// 提醒對話框列出三件且「已確認」鈕仍在畫面內 → 「回購物車」不成交 →
// 再按結帳 → 「已確認，繼續結帳」→ 成交。
// 需 backend:8000 + frontend:3000 已起、lucamp_e2e 已 seed（dev-manager + seed_dev_demo）。
// 流程最後會把那件商品賣掉 → **每次重跑前要重建 lucamp_e2e 並重新 seed**（docs/20 §1-2）。
import { mkdirSync } from "node:fs";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/inv-note-shots";
mkdirSync(SHOTS, { recursive: true });

// 三種庫存型態各一件，都寫備註：驗證三張表一致，且多筆時清單完整列出、
// 「已確認」鈕仍在畫面內按得到（seed 只留 1 件在庫序號品，故其餘取散裝與一般商品）。
const CASES = [
  { tab: "序號品", note: "缺營釘一支，交貨前要跟客人說" },
  { tab: "一般商品", note: "效期較短，請先進先出" },
  { tab: "散裝批", note: "數量請客人自己點過再收款" },
];

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

  // 2-4) 三種庫存型態各寫一筆備註，並確認各自列表出現摘要
  await page.click('a:has-text("庫存")');
  await page.waitForURL(`${BASE}/inventory`);
  await page.waitForSelector('[role="tab"]:has-text("序號品")');
  const codes = [];
  for (let i = 0; i < CASES.length; i += 1) {
    const { tab, note } = CASES[i];
    await page.locator(`[role="tab"]:has-text("${tab}")`).click();
    await page.waitForTimeout(700);
    if (tab === "序號品") {
      // 預設清單含已售出，改篩在庫（已售出雖可補備註，但要能掃進 POS）。
      await page.locator("select").first().selectOption("IN_STOCK");
      await page.waitForTimeout(900);
    }
    const rows = await page.locator("table tbody tr").count();
    if (rows === 0) {
      // 本腳本最後會把這些商品賣掉，故需要新鮮的 seed 才能重跑（見 docs/20 §1-2）。
      throw new Error(
        `「${tab}」沒有可售庫存——請先重建 lucamp_e2e 並重跑 seed_dev_demo`,
      );
    }
    const row = page.locator("table tbody tr").first();
    codes.push((await row.locator("td").first().innerText()).trim());

    await row.locator('button:has-text("詳細")').click();
    const detail = page.locator(
      '[aria-label="商品明細"], [aria-label="一般商品明細"], [aria-label="散裝批明細"]',
    );
    await detail.waitFor({ state: "visible", timeout: 6000 });
    await detail
      .locator('button:has-text("新增備註"), button:has-text("編輯備註")')
      .first()
      .click();
    const textarea = detail.locator('textarea[aria-label="商品備註"]');
    await textarea.waitFor({ state: "visible", timeout: 4000 });
    await textarea.fill(note);
    if (i === 0) await page.screenshot({ path: `${SHOTS}/01-note-edit.png` });
    await detail.locator('button:has-text("儲存備註")').click();
    await detail.locator(`text=${note}`).waitFor({ state: "visible", timeout: 6000 });
    if (i === 0) await page.screenshot({ path: `${SHOTS}/02-note-saved.png` });
    await detail.locator('button:has-text("關閉")').click();
    await detail.waitFor({ state: "hidden", timeout: 4000 });

    // 列表摘要：內部備忘要在列表就看得到，不必逐件點開
    const summary = await page.locator(".inv-note-summary").first().isVisible().catch(() => false);
    ok(`${tab}：寫入備註並在列表顯示摘要`, summary);
    if (i === 0) await page.screenshot({ path: `${SHOTS}/03-list-summary.png` });
  }
  ok("三種庫存型態都能寫備註", codes.length === CASES.length, codes.join(", "));

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
  for (const code of codes) {
    await page.fill('input[name="code"]', code);
    await page.press('input[name="code"]', "Enter");
    await page.waitForTimeout(700);
  }
  await page.locator(".pos-line-note").first().waitFor({ state: "visible", timeout: 8000 });
  const cartNoteCount = await page.locator(".pos-line-note").count();
  ok(
    `POS 購物車 ${CASES.length} 行都顯示備註`,
    cartNoteCount === CASES.length,
    `實得 ${cartNoteCount}`,
  );
  await page.screenshot({ path: `${SHOTS}/04-pos-cart-note.png` });

  // 6) 按結帳 → 跳提醒（尚未成交）
  await page.locator("button.pos-checkout").click();
  const dialog = page.locator('[aria-label="商品備註提醒"]');
  await dialog.waitFor({ state: "visible", timeout: 8000 });
  const listed = await dialog.locator(".pos-note-list li").count();
  ok(`提醒列出全部 ${CASES.length} 件`, listed === CASES.length, `實得 ${listed}`);
  for (const { note } of CASES) {
    const shown = await dialog.locator(`text=${note}`).isVisible().catch(() => false);
    ok(`提醒內容正確：${note.slice(0, 8)}…`, shown);
  }
  // 多筆時對話框不可長過畫面——「已確認」鈕必須仍在視窗內按得到。
  const confirmBtn = dialog.locator('button:has-text("已確認，繼續結帳")');
  const box = await confirmBtn.boundingBox();
  const viewport = page.viewportSize();
  ok(
    "多筆備註時「已確認」鈕仍在畫面內",
    box !== null && box.y >= 0 && box.y + box.height <= viewport.height,
    box ? `底邊 y=${Math.round(box.y + box.height)} / 視窗高 ${viewport.height}` : "找不到按鈕",
  );
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
  await confirmBtn.click();
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
