// 採購/補貨瀏覽器煙霧測試（Phase 5 /purchasing）：登入 → 低庫存提醒列出 seed 低庫存品 →
// 供應商分頁建立供應商 → 採購單分頁選供應商、搜尋數量品加入明細、填單價 → 建立採購單（已下單）→
// 收貨入庫（二次確認）→ 採購單變已收貨 + 低庫存品現量增加。
// 需 backend + frontend 已起、已 seed（dev-manager + seed_dev_purchasing）。
// 執行：LD_LIBRARY_PATH=... SMOKE_BASE=http://localhost:3000 node frontend/scripts/purchasing-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "purchasing");
mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  // 1) 登入
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(300);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  // 2) 進採購/補貨頁
  await page.click('a:has-text("採購補貨")');
  await page.waitForURL(`${BASE}/purchasing`);
  await page.waitForSelector("h1:has-text('採購 / 補貨')");
  ok("採購/補貨頁載入", true);

  // 3) 低庫存提醒：列出 seed 低庫存品（現量/補貨點）
  await page.waitForSelector(".pur-lowstock .pur-lowstock-list li");
  const lowText = (await page.locator(".pur-lowstock").innerText()) ?? "";
  const hasGas = lowText.includes("高山瓦斯罐 230g");
  const hasBrush = lowText.includes("爐具清潔刷");
  ok("低庫存提醒列出 seed 低庫存品", hasGas && hasBrush, "高山瓦斯罐 / 爐具清潔刷");
  ok("低庫存顯示現量/補貨點", lowText.includes("現量") && lowText.includes("補貨點"));
  await page.screenshot({ path: `${SHOTS}/01-lowstock.png`, fullPage: true });

  // 4) 供應商分頁：建立供應商 → 出現在清單
  const supplierName = "煙測供應商";
  await page.click('.settle-tabs button:has-text("供應商")');
  await page.waitForSelector(".pur-supplier-form");
  await page.fill('input[aria-label="供應商名稱"]', supplierName);
  await page.click('.pur-supplier-form button:has-text("新增供應商")');
  await page.waitForSelector(`.pur-supplier-list table tbody tr:has-text("${supplierName}")`);
  ok("建立供應商並出現在清單", true, supplierName);
  await page.screenshot({ path: `${SHOTS}/02-supplier.png`, fullPage: true });

  // 5) 採購單分頁：選供應商、搜尋數量品加入、填單價 → 建立採購單（已下單 + 收貨入庫鈕）
  await page.click('.settle-tabs button:has-text("採購單")');
  await page.waitForSelector(".pur-create");
  await page.selectOption('select[aria-label="供應商"]', { label: supplierName });
  await page.fill('input[aria-label="搜尋數量品"]', "瓦斯");
  // 從搜尋結果加入「高山瓦斯罐」這筆數量品
  await page.waitForSelector('.pur-search-results li button:has-text("高山瓦斯罐")');
  await page.click('.pur-search-results li button:has-text("高山瓦斯罐")');
  await page.waitForSelector(".pur-lines tbody tr");
  ok("搜尋數量品並加入明細", true, "高山瓦斯罐 230g");
  // 填進貨單價
  await page.fill('.pur-lines input[aria-label^="進貨單價"]', "100");
  await page.screenshot({ path: `${SHOTS}/03-draft-po.png`, fullPage: true });

  // 記錄建立前的採購單列數
  const beforePoCount = await page.locator(".pur-order-table tbody tr").count();
  await page.click('.pur-create button:has-text("建立採購單")');
  // 新採購單應出現（已下單 + 收貨入庫鈕）
  await page.waitForFunction(
    (n) => document.querySelectorAll(".pur-order-table tbody tr").length > n,
    beforePoCount,
  );
  const newRow = page
    .locator(".pur-order-table tbody tr")
    .filter({ has: page.locator('button:has-text("收貨入庫")') })
    .first();
  await newRow.waitFor();
  ok("採購單出現於清單", true);
  ok("採購單狀態為已下單", (await newRow.locator(".inv-badge").innerText()).includes("已下單"));
  ok("採購單有收貨入庫按鈕", (await newRow.locator('button:has-text("收貨入庫")').count()) === 1);
  await page.screenshot({ path: `${SHOTS}/04-po-ordered.png`, fullPage: true });

  // 6) 收貨入庫 → 二次確認 → 確認收貨 → 已收貨 + 低庫存品現量增加
  // 收貨前先記錄高山瓦斯罐目前現量（低庫存卡）
  const beforeGas = (
    (await page.locator(".pur-lowstock-list li:has-text('高山瓦斯罐 230g')").innerText().catch(() => "")) ?? ""
  ).match(/現量\s*(\d+)/)?.[1];

  await newRow.locator('button:has-text("收貨入庫")').click();
  await page.waitForSelector('[role="dialog"][aria-label="確認收貨"]');
  ok("收貨二次確認對話框跳出", true);
  await page.screenshot({ path: `${SHOTS}/05-receive-dialog.png`, fullPage: true });
  await page.click('[role="dialog"] button:has-text("確認收貨")');
  await page.waitForSelector('[role="dialog"]', { state: "detached" });

  // PO 變已收貨：同一供應商的列，狀態含「已收貨」且不再有收貨入庫鈕
  await page.waitForFunction(
    () =>
      [...document.querySelectorAll(".pur-order-table tbody tr")].some(
        (tr) =>
          tr.textContent?.includes("已收貨") &&
          !tr.querySelector("button"),
      ),
    undefined,
    { timeout: 8000 },
  );
  ok("採購單變為已收貨", true);

  // 低庫存卡刷新後現量應增加（建單數量預設 1）
  let gasIncreased = false;
  if (beforeGas !== undefined) {
    await page.waitForTimeout(800);
    const afterGas = (
      (await page
        .locator(".pur-lowstock-list li:has-text('高山瓦斯罐 230g')")
        .innerText()
        .catch(() => "")) ?? ""
    ).match(/現量\s*(\d+)/)?.[1];
    // 收貨後可能仍低於補貨點而留在卡上；若已補足則離開卡片（亦視為成功）
    gasIncreased =
      afterGas === undefined ? true : Number(afterGas) > Number(beforeGas);
    ok("收貨後現量增加", gasIncreased, `${beforeGas} → ${afterGas ?? "(已離開低庫存卡)"}`);
  } else {
    ok("收貨後現量增加", false, "收貨前讀不到高山瓦斯罐現量");
  }
  await page.screenshot({ path: `${SHOTS}/06-received.png`, fullPage: true });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
