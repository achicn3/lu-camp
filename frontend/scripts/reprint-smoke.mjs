// 補印入口煙霧：確認三種列印都補得回來（店主裁示 2026-08-21）。
//
// 盤點發現五種列印裡只有標籤與出餐單有補印入口；明細聯、發票證明聯、收購憑證聯
// 都只在「完成畫面」印得到，關掉就沒了。而 POS 的列印對話框還明講
// 「或日後在交易紀錄補印」——那個入口當時並不存在。
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const OUT = "/home/test/tmp/codex-test/reprint";
mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (n, pass, d = "") => { results.push({ n, pass }); console.log(`${pass ? "✅" : "❌"} ${n}${d ? "：" + d : ""}`); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  // ── 交易紀錄：明細聯與發票證明聯 ──
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/01-sales-buttons.png`, fullPage: false });
  const detailBtns = await page.locator('button[aria-label^="補印銷售"][aria-label$="商品明細聯"]').count();
  ok("交易紀錄：每列都有「補印明細聯」", detailBtns > 0, `${detailBtns} 顆`);
  const invoiceBtns = await page.locator('button[aria-label$="發票證明聯"]').count();
  ok("交易紀錄：已開立的單有「補印發票證明聯」", invoiceBtns > 0, `${invoiceBtns} 顆`);

  // ── 庫存：標籤補印（既有） ──
  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  ok("庫存：補印標籤", (await page.locator('button:has-text("補印標籤")').count()) > 0);

  // ── 交易紀錄：出餐單重印（既有） ──
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  ok("交易紀錄：重印出餐單", (await page.locator('button:has-text("重印出餐單")').count()) > 0);

  // ── 收購：憑證聯補印 ──
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${OUT}/02-acquisition-reprint.png`, fullPage: false });
  ok("收購：補印收購憑證聯面板", (await page.locator(".acq-reprint").count()) > 0);

  // 查一張不存在的單號，確認錯誤訊息看得懂
  await page.fill('input[aria-label="要補印的收購單號"]', "99999999");
  await page.click('.acq-reprint button:has-text("補印")');
  await page.waitForTimeout(2500);
  const msg = await page.locator(".acq-reprint .form-error, .acq-reprint .hint").last().innerText();
  await page.screenshot({ path: `${OUT}/03-acquisition-notfound.png`, fullPage: false });
  ok("收購：查無單號時訊息看得懂", /找不到/.test(msg), msg);
} catch (e) {
  ok("流程中斷", false, String(e));
  await page.screenshot({ path: `${OUT}/99-failure.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}
const passed = results.filter((r) => r.pass).length;
console.log(`\n結果：${passed}/${results.length} 通過\n截圖：${OUT}`);
if (passed !== results.length) process.exit(1);
