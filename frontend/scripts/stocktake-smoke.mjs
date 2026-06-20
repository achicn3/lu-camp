// 盤點瀏覽器煙霧測試（Phase 5 /stocktake）：登入 → 建立盤點單（快照數量品現量）→
// 逐項輸入實點數、即時差異（counted − system）與彙總 → 確認盤點調整（二次確認）→ 已確認 →
// 重開為唯讀並顯示最終差異。
// 需 backend + frontend 已起、已 seed（dev-manager + seed_dev_purchasing）。
// 執行：LD_LIBRARY_PATH=... SMOKE_BASE=http://localhost:3000 node frontend/scripts/stocktake-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "stocktake");
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

  // 2) 進盤點頁
  await page.click('a:has-text("盤點")');
  await page.waitForURL(`${BASE}/stocktake`);
  await page.waitForSelector("h1:has-text('盤點')");
  ok("盤點頁載入", true);

  // 3) 建立盤點單 → 明細顯示數量品列（系統現量 + 實點數輸入）
  await page.click('button:has-text("建立盤點單")');
  await page.waitForSelector(".st-detail .st-lines tbody tr");
  const lineCount = await page.locator(".st-detail .st-lines tbody tr").count();
  ok("建立盤點單並開啟明細", lineCount >= 5, `${lineCount} 列`);
  ok(
    "每列有系統現量與實點數輸入",
    (await page.locator('.st-lines input[aria-label^="實點數"]').count()) === lineCount,
  );
  ok("狀態為盤點中", (await page.locator(".st-detail .inv-badge").innerText()).includes("盤點中"));
  await page.screenshot({ path: `${SHOTS}/01-draft-detail.png`, fullPage: true });

  // 4) 對第一列輸入與系統不同的實點數 → 差異即時顯示正確帶號值（counted − system）
  const firstRow = page.locator(".st-lines tbody tr").first();
  const systemQty = Number((await firstRow.locator("td").nth(1).innerText()).trim());
  const counted = systemQty + 3; // 盤盈 +3
  await firstRow.locator('input[aria-label^="實點數"]').fill(String(counted));
  // 差異欄即時更新
  await page.waitForFunction(
    (expected) => {
      const cell = document
        .querySelector(".st-lines tbody tr")
        ?.querySelector(".st-var");
      return cell?.textContent?.trim() === expected;
    },
    `+${counted - systemQty}`,
    { timeout: 4000 },
  );
  const varText = (await firstRow.locator(".st-var").innerText()).trim();
  ok("差異即時顯示正確帶號值", varText === `+${counted - systemQty}`, `系統 ${systemQty} → 實點 ${counted}，差異 ${varText}`);
  // 彙總更新（淨差異含 +3）
  const summaryText = (await page.locator(".st-summary").innerText()) ?? "";
  ok("彙總更新", summaryText.includes("盤盈 +3") || summaryText.includes("+3"), summaryText.replace(/\s+/g, " ").trim());
  await page.screenshot({ path: `${SHOTS}/02-variance.png`, fullPage: true });

  // 5) 確認盤點調整 → 二次確認 → 確認調整 → 已確認
  await page.click('.st-detail button:has-text("確認盤點調整")');
  await page.waitForSelector('[role="dialog"][aria-label="確認盤點"]');
  ok("盤點二次確認對話框跳出", true);
  await page.screenshot({ path: `${SHOTS}/03-confirm-dialog.png`, fullPage: true });
  await page.click('[role="dialog"] button:has-text("確認調整")');
  await page.waitForSelector('[role="dialog"]', { state: "detached" });
  await page.waitForFunction(
    () => document.querySelector(".st-detail .inv-badge")?.textContent?.includes("已確認"),
    undefined,
    { timeout: 8000 },
  );
  ok("盤點單變為已確認", true);
  await page.screenshot({ path: `${SHOTS}/04-confirmed.png`, fullPage: true });

  // 6) 返回清單再重開 → 唯讀（無實點數輸入）且顯示最終差異
  await page.click('.st-detail button:has-text("返回清單")');
  await page.waitForSelector(".st-list table tbody tr");
  await page.locator('.st-list table tbody tr:has-text("已確認") button:has-text("檢視")').first().click();
  await page.waitForSelector(".st-detail .st-lines tbody tr");
  const inputsAfter = await page.locator('.st-lines input[aria-label^="實點數"]').count();
  ok("已確認盤點單唯讀（無實點數輸入）", inputsAfter === 0);
  const finalVar = (await page.locator(".st-lines tbody tr").first().locator(".st-var").innerText()).trim();
  ok("唯讀檢視顯示最終差異", finalVar === `+${counted - systemQty}`, finalVar);
  await page.screenshot({ path: `${SHOTS}/05-readonly.png`, fullPage: true });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
