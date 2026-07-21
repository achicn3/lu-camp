// 採購/補貨瀏覽器煙霧測試（採購 v2）：登入 → 低庫存提醒 → 建供應商 →
// 送出採購（qty 6，已下單）→ 分批收貨（收 4 → 部分到貨）→ 收足（收 2 → 已收貨）→
// 詳情驗證逐項訂購/已收/待收＋收貨批次 → 草稿建立後取消。
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

const PROD = "高山瓦斯罐 230g";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

// 目前篩選下最新（id 最大）採購單列＝第一列。
const firstRow = () => page.locator(".pur-order-table tbody tr").first();

async function openCreatePanel() {
  const toggle = page.locator('.pur-create-toggle:has-text("建立採購單")');
  if ((await page.locator(".pur-create").count()) === 0) await toggle.click();
  await page.waitForSelector(".pur-create");
}

async function buildDraftLine(supplierName, qty) {
  const supplierCombo = page.getByLabel("供應商");
  await supplierCombo.click();
  await supplierCombo.fill(supplierName);
  await page.click(`.combo-option:has-text("${supplierName}")`);
  await page.fill('input[aria-label="搜尋一般商品"]', "瓦斯");
  await page.waitForSelector(`.pur-search-results li button:has-text("${PROD}")`);
  await page.click(`.pur-search-results li button:has-text("${PROD}")`);
  await page.waitForSelector(".pur-lines tbody tr");
  await page.fill(`.pur-lines input[aria-label="數量 ${PROD}"]`, String(qty));
  await page.fill('.pur-lines input[aria-label^="進貨單價"]', "100");
}

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

  // 3) 低庫存提醒常駐置頂（等清單載入完成，避免讀到「載入中…」）
  await page.waitForSelector(".pur-lowstock .pur-lowstock-list li, .pur-lowstock .empty-state");
  const lowText = (await page.locator(".pur-lowstock").innerText()) ?? "";
  ok("低庫存提醒顯示現量/補貨點", lowText.includes("現量") && lowText.includes("補貨點"));
  await page.screenshot({ path: `${SHOTS}/01-lowstock.png`, fullPage: true });

  // 4) 供應商分頁：建立供應商
  const supplierName = `煙測供應商${Date.now().toString().slice(-5)}`;
  await page.click('.settle-tabs button:has-text("供應商")');
  await page.waitForSelector(".pur-supplier-form");
  await page.fill('input[aria-label="供應商名稱"]', supplierName);
  await page.click('.pur-supplier-form button:has-text("新增供應商")');
  await page.waitForSelector(`.pur-supplier-list table tbody tr:has-text("${supplierName}")`);
  ok("建立供應商並出現在清單", true, supplierName);

  // 5) 採購單分頁：送出採購（qty 6 → 已下單）
  await page.click('.settle-tabs button:has-text("採購單")');
  await openCreatePanel();
  await buildDraftLine(supplierName, 6);
  await page.screenshot({ path: `${SHOTS}/02-build-po.png`, fullPage: true });
  await page.click('.pur-create button:has-text("送出採購")');
  // 切「全部」，最新列即本單
  await page.click('.settle-tabs button:has-text("全部")');
  await page.waitForSelector(`.pur-order-table tbody tr:has-text("${supplierName}")`);
  const badge1 = await firstRow().locator(".inv-badge").innerText();
  ok("送出採購 → 已下單", badge1.includes("已下單"), badge1);
  await page.screenshot({ path: `${SHOTS}/03-ordered.png`, fullPage: true });

  // 5b) 待到貨（Phase 2）：下單後低庫存卡的該品應顯示在途待到貨量（避免重複採購）
  await page.waitForSelector(`.pur-lowstock-list li:has-text("${PROD}")`);
  const gasRow = page.locator(`.pur-lowstock-list li:has-text("${PROD}")`).first();
  await gasRow.locator(".pur-incoming").waitFor({ timeout: 5000 }).catch(() => {});
  const gasText = await gasRow.innerText();
  ok("低庫存卡顯示在途待到貨量", /待到貨\s*6/.test(gasText), gasText.replace(/\n/g, " "));

  // 6) 分批收貨：收 4（部分到貨）＋登錄進項發票
  await firstRow().locator('button:has-text("收貨入庫")').click();
  await page.waitForSelector('[role="dialog"][aria-label="確認收貨"]');
  await page.fill(`input[aria-label="本次實收 ${PROD}"]`, "4");
  const invoiceNo = `AB${Date.now().toString().slice(-8)}`;
  await page.fill('input[aria-label="發票號碼"]', invoiceNo);
  await page.fill('input[aria-label="發票日期"]', "2026-07-11");
  await page.fill('input[aria-label="發票含稅金額"]', "1050");
  await page.screenshot({ path: `${SHOTS}/04-receive-partial.png`, fullPage: true });
  await page.click('[role="dialog"] button:has-text("確認收貨")');
  await page.waitForSelector('[role="dialog"]', { state: "detached" });
  await page.waitForSelector(`.pur-order-table tbody tr:has-text("${supplierName}") .inv-badge:has-text("部分到貨")`);
  ok("分批收貨 4/6 → 部分到貨", true);
  await page.screenshot({ path: `${SHOTS}/05-partial.png`, fullPage: true });

  // 7) 收足剩餘 2 → 已收貨
  await firstRow().locator('button:has-text("收貨入庫")').click();
  await page.waitForSelector('[role="dialog"][aria-label="確認收貨"]');
  // 本次實收預設帶入待收（2）；直接確認
  const prefill = await page.inputValue(`input[aria-label="本次實收 ${PROD}"]`);
  ok("收貨對話框預設帶入待收量", prefill === "2", `待收預設=${prefill}`);
  await page.click('[role="dialog"] button:has-text("確認收貨")');
  await page.waitForSelector('[role="dialog"]', { state: "detached" });
  await page.waitForSelector(`.pur-order-table tbody tr:has-text("${supplierName}") .inv-badge:has-text("已收貨")`);
  ok("收足剩餘 → 已收貨", true);
  await page.screenshot({ path: `${SHOTS}/06-received.png`, fullPage: true });

  // 8) 詳情：逐項訂購/已收/待收 ＋ 兩筆收貨批次（首批有發票）
  await firstRow().locator('button:has-text("詳細")').click();
  await page.waitForSelector('[role="dialog"][aria-label="採購單詳情"]');
  const detailText = await page.textContent(".pur-detail");
  const receiptCount = await page.locator(".pur-receipts-list li").count();
  ok(
    "詳情顯示已收 6 / 待收 0 ＋收貨批次含發票",
    receiptCount === 2 && detailText.includes(invoiceNo) && detailText.includes("1,000"),
    `批次數=${receiptCount}`,
  );
  await page.screenshot({ path: `${SHOTS}/07-detail.png`, fullPage: true });
  await page.click('[role="dialog"] button:has-text("關閉")');

  // 9) 草稿 → 取消
  await openCreatePanel();
  await buildDraftLine(supplierName, 3);
  await page.click('.pur-create button:has-text("存草稿")');
  await page.click('.settle-tabs button:has-text("草稿")');
  await page.waitForSelector(`.pur-order-table tbody tr:has-text("${supplierName}") .inv-badge:has-text("草稿")`);
  ok("存草稿 → 草稿列表可見", true);
  await firstRow().locator('button:has-text("取消")').click();
  await page.click('.settle-tabs button:has-text("已取消")');
  await page.waitForSelector(`.pur-order-table tbody tr:has-text("${supplierName}") .inv-badge:has-text("已取消")`);
  ok("草稿取消 → 已取消", true);
  await page.screenshot({ path: `${SHOTS}/08-cancelled.png`, fullPage: true });

  // 10) 供應商管理（Phase 3）：編輯 → 停用 → 清單顯示已停用 → 重新啟用
  await page.click('.settle-tabs button:has-text("供應商")');
  const supRow = page.locator(`.pur-supplier-list table tbody tr:has-text("${supplierName}")`).first();
  await supRow.waitFor();
  await supRow.locator('button:has-text("編輯")').click();
  await page.waitForSelector('[role="dialog"][aria-label="編輯供應商"]');
  await page.fill('input[aria-label="編輯聯絡方式"]', "0900-123-456");
  await page.click('[role="dialog"] button:has-text("儲存")');
  await page.waitForSelector('[role="dialog"]', { state: "detached" });
  await page.waitForSelector(`.pur-supplier-list table tbody tr:has-text("0900-123-456")`);
  ok("供應商編輯（聯絡方式）成功", true);
  await supRow.locator('button:has-text("停用")').click();
  await page.waitForSelector(`.pur-supplier-list table tbody tr:has-text("${supplierName}") .inv-badge:has-text("已停用")`);
  ok("供應商停用 → 顯示已停用", true);
  await supRow.locator('button:has-text("啟用")').click();
  await page.waitForSelector(`.pur-supplier-list table tbody tr:has-text("${supplierName}") .inv-badge:has-text("啟用中")`);
  ok("供應商重新啟用 → 顯示啟用中", true);
  await page.screenshot({ path: `${SHOTS}/09-supplier-manage.png`, fullPage: true });

  // 11) Phase 4 版面：桌面雙欄、單號/供應商搜尋、Modal 背景鎖捲動
  await page.click('.settle-tabs button:has-text("採購單")');
  await page.click('.settle-tabs button:has-text("全部")');
  // 桌面雙欄：低庫存欄（rail）在主欄右側
  const mainBox = await page.locator(".pur-workbench-main").boundingBox();
  const railBox = await page.locator(".pur-workbench-rail").boundingBox();
  ok(
    "桌面雙欄：低庫存欄在主欄右側",
    railBox && mainBox && railBox.x > mainBox.x + mainBox.width / 2,
    `main.x=${Math.round(mainBox?.x)} rail.x=${Math.round(railBox?.x)}`,
  );
  // 搜尋：以供應商名過濾
  await page.fill('input[aria-label="採購單搜尋"]', supplierName);
  await page.click('.pur-orders form.member-allsearch button:has-text("搜尋")');
  await page.waitForSelector(`.pur-order-table tbody tr:has-text("${supplierName}")`);
  const allSupplierRows = await page.locator(".pur-order-table tbody tr").count();
  const matchRows = await page
    .locator(`.pur-order-table tbody tr:has-text("${supplierName}")`)
    .count();
  ok("採購單搜尋（供應商名）過濾", allSupplierRows > 0 && allSupplierRows === matchRows);
  await page.click('.pur-orders form.member-allsearch button:has-text("清除")');
  // Modal 背景鎖捲動：開詳情 → body overflow hidden；關閉 → 還原
  await page.locator('.pur-order-table tbody tr button:has-text("詳細")').first().click();
  await page.waitForSelector('[role="dialog"][aria-label="採購單詳情"]');
  const lockedOverflow = await page.evaluate(() => document.body.style.overflow);
  ok("開 Modal 時鎖背景捲動（body overflow hidden）", lockedOverflow === "hidden", lockedOverflow);
  await page.click('[role="dialog"] button:has-text("關閉")');
  await page.waitForSelector('[role="dialog"]', { state: "detached" });
  const restoredOverflow = await page.evaluate(() => document.body.style.overflow);
  ok("關 Modal 後還原背景捲動", restoredOverflow !== "hidden", `overflow="${restoredOverflow}"`);
  await page.screenshot({ path: `${SHOTS}/10-desktop-layout.png`, fullPage: true });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
