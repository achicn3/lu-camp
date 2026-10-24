// 一般商品手動改數量煙霧測試（店主 2026-10-24）：登入 → 庫存「一般商品」→ 編輯 →
// 破損卻要增加被擋 → 改成減少一件（破損）→ 清單數量變了 → 詳細歷史出現「破損」。
// 需 backend + frontend 已起、已 seed（dev-manager + seed_dev_purchasing）。
// 執行：SMOKE_BASE=http://localhost:3000 node frontend/scripts/catalog-quantity-adjust-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "catalog-quantity-adjust");
mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));
await skipOpeningCheckRedirect(page);

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(300);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  await page.click('[role="tab"]:has-text("一般商品")');
  // 找第一列現量 ≥ 1 的商品
  await page.waitForSelector("table tbody tr");
  const rows = page.locator("table tbody tr");
  let row = null;
  let before = 0;
  for (let i = 0; i < (await rows.count()); i += 1) {
    const r = rows.nth(i);
    const dialogInput = r.locator('button:has-text("編輯")');
    if ((await dialogInput.count()) === 0) continue;
    await dialogInput.click();
    const value = Number(await page.locator('[aria-label="新數量"]').inputValue());
    if (value >= 1) {
      row = r;
      before = value;
      break;
    }
    await page.click('[role="dialog"] button:has-text("取消")');
  }
  ok("找到有庫存的一般商品並開啟編輯", row !== null, `現量 ${before}`);
  const productName = (await row.locator("td").nth(2).innerText()).trim(); // 欄位：編號、品牌、品名…
  await page.screenshot({ path: `${SHOTS}/01-edit-dialog.png`, fullPage: true });

  // 破損卻要增加 → 擋下
  await page.fill('[aria-label="新數量"]', String(before + 1));
  await page.selectOption('[aria-label="調整原因"]', "DAMAGED");
  await page.click('[role="dialog"] button:has-text("更新數量")');
  await page.waitForSelector('[role="dialog"] :text("破損、過期、自用只能減少數量")');
  ok("破損不能增加數量", true);
  await page.screenshot({ path: `${SHOTS}/02-damaged-increase-blocked.png`, fullPage: true });

  // 減少一件（破損）
  await page.fill('[aria-label="新數量"]', String(before - 1));
  await page.fill('[aria-label="調整備註"]', "煙霧測試");
  await page.click('[role="dialog"] button:has-text("更新數量")');
  await page.waitForSelector('[role="dialog"][aria-label="編輯商品"]', { state: "detached" });
  ok("更新數量後關閉視窗", true);
  await page.waitForTimeout(500);
  const updatedRow = page.locator(`table tbody tr:has-text("${productName}")`).first();
  const rowText = await updatedRow.innerText();
  ok("清單列仍在", rowText.includes(productName), rowText.replace(/\s+/g, " ").slice(0, 80));
  await page.screenshot({ path: `${SHOTS}/03-list-after.png`, fullPage: true });

  // 詳細 → 歷史有「破損」、現量少一件
  await updatedRow.locator('button:has-text("詳細")').click();
  await page.waitForSelector('[role="dialog"]');
  await page.waitForSelector('[role="dialog"] :text("破損")');
  const detailText = await page.locator('[role="dialog"]').innerText();
  ok("明細歷史出現「破損」", detailText.includes("破損"));
  ok("明細現量少一件", detailText.includes(`${before - 1} /`), `應為 ${before - 1}`);
  await page.screenshot({ path: `${SHOTS}/04-detail-history.png`, fullPage: true });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
