// 完整端對端「人類操作」煙霧：以單一受測資料集，依真實門市作業順序逐一點擊 UI、逐步截圖。
//
// 劇本（同一位主角會員「林大山」貫穿全程）：
//   1) 登入(MANAGER) → 2) 開帳(零用金) → 3) 建檔會員/賣方/寄售人（同一人）→
//   4) 買斷#1（品牌/型號/分類「查無即建」autocomplete 截圖 → 現金收購 → 標籤列印）→
//   5) 買斷#2（品牌 autocomplete「查既有」截圖 → 以購物金撥款，讓會員取得購物金）→
//   6) 寄售一件（抽成 50%）→ 7) 餐飲菜單管理（新增手沖咖啡）→
//   8) 庫存頁逐列補印標籤 → 9) POS：二手＋餐飲同車＋會員＋購物金（示範「內用不可折抵購物金」上限）→
//   10) POS：賣出寄售品（產生待付款結算）→ 11) 寄售付款（付給林大山）→
//   12) 報表：今日營運/趨勢(餐飲二手分列)/現金對帳/銷售毛利/庫存價值/寄售應付 →
//   13) 關帳（實點現金、差異）。
//
// 需 backend(:8000)+frontend(:3000)+hardware-agent(:8001) 已起、DB 已 migrate 並 seed（門市 + dev-manager）。
// 執行：見檔尾說明（需 LD_LIBRARY_PATH 指向 pwlibs）。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "full-e2e");
mkdirSync(SHOTS, { recursive: true });

const RUN = Date.now().toString().slice(-6);
const MEMBER_NAME = `林大山-${RUN}`;
const MEMBER_PHONE = `09${RUN}0000`.slice(0, 10);
const MEMBER_NID = `E2E${RUN}X`;

const results = [];
let shotN = 0;
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}
async function shot(page, slug) {
  shotN += 1;
  const n = String(shotN).padStart(2, "0");
  await page.screenshot({ path: `${SHOTS}/${n}-${slug}.png`, fullPage: true });
  console.log(`   📸 ${n}-${slug}.png`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 1000 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

// ── 共用小工具 ────────────────────────────────────────────────
async function login() {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(300);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
}

async function nav(label, urlPart) {
  await page.click(`a:has-text("${label}")`);
  await page.waitForURL(`${BASE}${urlPart}`);
}

// CreatableCombobox：開選單、輸入、點「建立『X』」（查無即建）。
async function comboCreate(label, value) {
  const input = page.getByLabel(label, { exact: true });
  await input.click();
  await input.fill(value);
  await page.waitForTimeout(450); // debounce 200ms + 查詢
  const createBtn = page.locator(".combo-menu .combo-create").filter({ hasText: value });
  await createBtn.waitFor({ state: "visible", timeout: 8000 });
  return input;
}

// CreatableCombobox：開選單、輸入前綴、點既有選項（驗證 autocomplete 查既有）。
async function comboPickExisting(label, typed, optionText) {
  const input = page.getByLabel(label, { exact: true });
  await input.click();
  await input.fill(typed);
  await page.waitForTimeout(450);
  const opt = page.locator(".combo-menu .combo-option").filter({ hasText: optionText }).first();
  await opt.waitFor({ state: "visible", timeout: 8000 });
  return { input, opt };
}

async function selectSeller(name) {
  const search = page.getByLabel("賣方搜尋", { exact: true });
  await search.click();
  await search.fill(name);
  await page.waitForTimeout(500);
  await page.locator(".acq-results .combo-option").filter({ hasText: name }).first().click();
}

function codesFrom(text) {
  // 序號條碼形如 S1-26E77BAFE3；result 文字後面接「列印標籤」按鈕字樣，故以 token 正則精準擷取。
  return [...text.matchAll(/S\d+-[0-9A-F]+/g)].map((m) => m[0]);
}

// ── 主流程 ───────────────────────────────────────────────────
let buyoutCode = null; // 買斷#1 序號（POS 賣二手用）
let consignCode = null; // 寄售品序號（POS 賣寄售用）

try {
  // 1) 登入
  await login();
  ok("1) 登入成功（MANAGER）", true);
  await shot(page, "home");

  // 2) 開帳
  await nav("現金對帳", "/cash");
  await page.waitForSelector('input[name="opening_float"], .badge-open', { timeout: 8000 });
  if (await page.locator('input[name="opening_float"]').count()) {
    await page.fill('input[name="opening_float"]', "3000");
    await page.click('button:has-text("開帳")');
    await page.waitForSelector(".badge-open", { timeout: 8000 });
    ok("2) 開帳成功（零用金 3,000）", true);
  } else {
    ok("2) 已在開帳中", true);
  }
  await shot(page, "cash-open");

  // 3) 建檔主角會員（會員＋賣方＋寄售人）
  await nav("會員/賣方", "/contacts");
  await page.waitForSelector('input[name="name"]');
  await page.fill('input[name="name"]', MEMBER_NAME);
  await page.fill('input[name="phone"]', MEMBER_PHONE);
  await page.fill('input[name="national_id"]', MEMBER_NID);
  // 角色：MEMBER 預設已勾；補勾 賣方、寄售人
  for (const role of ["賣方", "寄售人"]) {
    const cb = page.locator(".member-role-check", { hasText: role }).locator('input[type="checkbox"]');
    if (!(await cb.isChecked())) await cb.check();
  }
  await shot(page, "member-form");
  await page.click('button:has-text("建檔")');
  await page.waitForTimeout(800);
  // 以搜尋確認建檔成功
  await page.locator(".member-search input").fill(MEMBER_NAME);
  await page.click('.member-search button:has-text("搜尋")');
  await page.waitForSelector(`.member-row:has-text("${MEMBER_NAME}")`, { timeout: 8000 });
  ok("3) 會員建檔成功（會員/賣方/寄售人）", true, MEMBER_NAME);
  await shot(page, "member-created");

  // 4) 買斷#1：autocomplete 查無即建（品牌/型號/分類）→ 現金收購 → 標籤列印
  await nav("收購", "/acquisition");
  await page.click('[role="tab"]:has-text("買斷")');
  await selectSeller(MEMBER_NAME);
  ok("4) 收購選取既有賣方（林大山）", true);
  await page.fill('input[aria-label="品名"]', "Snow Peak 帳篷");
  // 品牌：查無 → 截圖「建立『Snow Peak』」
  await comboCreate("品牌", "Snow Peak");
  ok("4) 品牌 autocomplete：查無即建（建立鈕出現）", true);
  await shot(page, "brand-create");
  await page.locator(".combo-menu .combo-create").filter({ hasText: "Snow Peak" }).click();
  // 型號：依品牌 → 查無 → 截圖
  await comboCreate("型號", "Amenity Dome");
  ok("4) 型號 autocomplete：查無即建（依品牌啟用）", true);
  await shot(page, "model-create");
  await page.locator(".combo-menu .combo-create").filter({ hasText: "Amenity Dome" }).click();
  // 分類：查無 → 建立（連帶定價規則）
  await comboCreate("分類", "帳篷");
  await page.locator(".combo-menu .combo-create").filter({ hasText: "帳篷" }).click();
  ok("4) 分類 autocomplete：查無即建", true);
  // 成色、定價
  await page.locator(".acq-row select").first().selectOption("A");
  await page.fill('input[aria-label="估計轉售價"]', "2400");
  await page.fill('input[aria-label="收購價"]', "900");
  await page.fill('input[aria-label="上架售價"]', "1800");
  await shot(page, "buyout1-filled");
  // 現金撥款（預設 CASH，已開帳）→ 送出
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector(".acq-result", { timeout: 10000 });
  const r1 = (await page.locator(".acq-result").textContent()) ?? "";
  buyoutCode = codesFrom(r1)[0] ?? null;
  ok("4) 現金買斷完成（取得序號）", buyoutCode !== null, buyoutCode ?? r1.slice(0, 60));
  await shot(page, "buyout1-done");
  // 標籤列印（經 hardware-agent）
  const printBtn = page.locator(".acq-result button").filter({ hasText: /列印|標籤|補印/ }).first();
  if (await printBtn.count()) {
    await printBtn.click();
    await page.waitForTimeout(1500);
    ok("4) 收購後標籤列印（送代理）", true);
    await shot(page, "buyout1-label");
  }

  // 5) 買斷#2：品牌 autocomplete「查既有」→ 以購物金撥款（會員取得購物金）
  await nav("收購", "/acquisition");
  await page.click('[role="tab"]:has-text("買斷")');
  await selectSeller(MEMBER_NAME);
  await page.fill('input[aria-label="品名"]', "Snow Peak 焚火台");
  // 品牌：輸入「Snow」→ 既有「Snow Peak」出現於下拉（autocomplete 查既有）
  const { opt } = await comboPickExisting("品牌", "Snow", "Snow Peak");
  ok("5) 品牌 autocomplete：查既有（下拉出現 Snow Peak）", true);
  await shot(page, "brand-autocomplete-existing");
  await opt.click();
  await comboPickExisting("型號", "Ame", "Amenity Dome").then(({ opt: o }) => o.click());
  await comboPickExisting("分類", "帳", "帳篷").then(({ opt: o }) => o.click());
  await page.locator(".acq-row select").first().selectOption("B");
  await page.fill('input[aria-label="估計轉售價"]', "3000");
  await page.fill('input[aria-label="收購價"]', "2500");
  await page.fill('input[aria-label="上架售價"]', "3000");
  // 撥款改「購物金」
  await page.locator(".acq-payout-mode", { hasText: "購物金" }).click();
  await page.waitForSelector(".acq-premium");
  ok("5) 購物金撥款提示（含溢價）", await page.locator(".acq-premium").isVisible());
  await shot(page, "buyout2-storecredit");
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector(".acq-result", { timeout: 10000 });
  ok("5) 購物金買斷完成（會員取得購物金）", true);
  await shot(page, "buyout2-done");

  // 6) 寄售一件（抽成 50%）
  await nav("收購", "/acquisition");
  await page.click('[role="tab"]:has-text("寄售")');
  await selectSeller(MEMBER_NAME);
  await page.fill('input[aria-label="品名"]', "Coleman 寄售汽化爐");
  await comboPickExisting("品牌", "Snow", "Snow Peak").then(({ opt: o }) => o.click());
  await comboPickExisting("分類", "帳", "帳篷").then(({ opt: o }) => o.click());
  await page.locator(".acq-row select").first().selectOption("A");
  await page.locator('label:has-text("抽成") input').fill("50");
  await page.fill('input[aria-label="上架售價"]', "1000");
  await shot(page, "consign-filled");
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector(".acq-result", { timeout: 10000 });
  const r3 = (await page.locator(".acq-result").textContent()) ?? "";
  consignCode = codesFrom(r3)[0] ?? null;
  ok("6) 寄售入庫完成（取得序號）", consignCode !== null, consignCode ?? r3.slice(0, 60));
  await shot(page, "consign-done");

  // 7) 餐飲菜單管理：新增手沖咖啡（POS 餐飲磚用）
  await nav("餐飲菜單", "/menu");
  await page.waitForSelector(".inv-table");
  const coffeeName = `手沖咖啡-${RUN}`;
  await page.getByLabel("品名").fill(coffeeName);
  await page.getByLabel("售價（整數元）").fill("120");
  await page.getByLabel("分類（選填）").fill("飲品");
  await page.click('button:has-text("新增品項")');
  await page.waitForSelector(`tr:has-text("${coffeeName}")`, { timeout: 8000 });
  ok("7) 餐飲菜單新增手沖咖啡", true, coffeeName);
  await shot(page, "menu-created");

  // 8) 庫存頁逐列補印標籤
  await nav("庫存", "/inventory");
  await page.waitForSelector('[role="tab"]:has-text("序號品")');
  const reprint = page.locator(".inv-table tbody tr .inv-reprint-btn").first();
  await reprint.waitFor({ timeout: 8000 });
  await shot(page, "inventory-list");
  await reprint.click();
  await page.waitForSelector(".inv-reprint-ok, .inv-reprint-err", { timeout: 15000 });
  ok("8) 庫存逐列補印標籤（送代理）", (await page.locator(".inv-reprint-ok").count()) > 0);
  await shot(page, "inventory-reprint");

  // 9) POS：二手＋餐飲同車＋會員＋購物金（示範內用不可折抵購物金上限）
  await nav("POS 結帳", "/pos");
  await page.waitForSelector(".pos-menu-tiles");
  // 二手：掃序號
  await page.fill('input[name="code"]', buyoutCode);
  await page.press('input[name="code"]', "Enter");
  await page.waitForSelector(".pos-cart", { timeout: 8000 });
  ok("9) POS 掃入二手序號品", true, buyoutCode);
  // 餐飲：點磚 → 數量 1 → 加入
  const tile = page.locator(".pos-menu-tile").filter({ hasText: `手沖咖啡-${RUN}` }).first();
  await tile.click();
  const dlg = page.locator('[role="dialog"]');
  await dlg.waitFor();
  await dlg.getByRole("button", { name: "加入購物車" }).click();
  await page.waitForTimeout(400);
  ok("9) POS 加入餐飲（手沖咖啡）", true);
  await shot(page, "pos-mixed-cart");
  // 歸戶會員
  await page.locator(".pos-member-search input").fill(MEMBER_NAME);
  await page.click('button:has-text("查詢會員")');
  await page.locator(".pos-member-results button").filter({ hasText: MEMBER_NAME }).first().click();
  await page.waitForSelector(".pos-member-selected .money", { timeout: 8000 });
  ok("9) POS 會員歸戶（購物金餘額載入）", true);
  // 選「購物金」→ 應出現上限阻擋（內用不可折抵）
  await page.locator(".pos-tender-mode", { hasText: "購物金" }).click();
  const capErr = page.locator('[role="alert"].form-error').filter({ hasText: /餐飲不可用購物金折抵/ });
  await capErr.waitFor({ state: "visible", timeout: 8000 });
  ok("9) ★內用不可折抵購物金（上限阻擋）", true, (await capErr.textContent()) ?? "");
  await shot(page, "pos-storecredit-cap");
  // 改「混合」：現金部分 = 餐飲小計 120，其餘以購物金 → 可結帳
  await page.locator(".pos-tender-mode", { hasText: "混合" }).click();
  await page.locator('label:has-text("現金部分") input').fill("120");
  await page.waitForTimeout(400);
  ok("9) 改混合付款（現金 120 + 購物金抵二手）", await page.locator("text=購物金扣抵").isVisible());
  await shot(page, "pos-mixed-tender");
  await page.locator(".pos-checkout").click();
  await page.waitForSelector("text=已完成", { timeout: 10000 });
  ok("9) ★二手＋餐飲＋購物金 結帳完成", true);
  await shot(page, "pos-mixed-done");
  // 收掉列印對話框
  const pd = page.locator('[role="dialog"]');
  if (await pd.isVisible()) {
    const done = pd.locator("button").filter({ hasText: /完成|關閉/ }).last();
    if (await done.count()) await done.click();
  }

  // 10) POS：賣出寄售品（現金）→ 產生待付款結算
  await page.locator('button:has-text("開始下一筆")').click().catch(() => {});
  await page.waitForSelector('input[name="code"]', { timeout: 8000 });
  await page.fill('input[name="code"]', consignCode);
  await page.press('input[name="code"]', "Enter");
  await page.waitForSelector(".pos-cart", { timeout: 8000 });
  await page.locator(".pos-tender-mode", { hasText: "現金" }).click();
  await page.locator(".pos-checkout").click();
  await page.waitForSelector("text=已完成", { timeout: 10000 });
  ok("10) 賣出寄售品完成（產生待付款結算）", true);
  await shot(page, "pos-consign-sold");
  const pd2 = page.locator('[role="dialog"]');
  if (await pd2.isVisible()) {
    const done = pd2.locator("button").filter({ hasText: /完成|關閉/ }).last();
    if (await done.count()) await done.click();
  }

  // 11) 寄售付款：付給林大山
  await nav("寄售付款", "/consignment");
  await page.waitForSelector("table.settle-table tbody tr", { timeout: 8000 });
  await shot(page, "consign-pending");
  const payRow = page.locator(`table.settle-table tbody tr:has-text("${MEMBER_NAME}")`).first();
  const payBtn = (await payRow.count())
    ? payRow.locator('button:has-text("付款")')
    : page.locator('table.settle-table tbody tr button:has-text("付款")').first();
  await payBtn.click();
  await page.waitForSelector('[role="dialog"][aria-label="確認付款"]', { timeout: 8000 });
  await page.click('[role="dialog"] button:has-text("確認付款")');
  await page.waitForSelector('[role="dialog"]', { state: "detached", timeout: 8000 });
  ok("11) 寄售付款完成（現金出帳）", true);
  await shot(page, "consign-paid");

  // 12) 報表巡覽
  await nav("報表", "/reports");
  await page.waitForSelector(".rpt-dashboard-cards", { timeout: 8000 });
  ok(
    "12) 今日營運：餐飲/二手分列卡",
    (await page.locator("dt:has-text('餐飲營收')").count()) > 0 &&
      (await page.locator("dt:has-text('二手營收')").count()) > 0,
  );
  await shot(page, "report-dashboard");
  // 趨勢（餐飲營收線 + 餐飲/二手欄）
  await page.click('[role="tab"]:has-text("趨勢")');
  await page.waitForSelector(".rpt-trend-chart", { timeout: 8000 });
  await page.waitForTimeout(800);
  ok(
    "12) ★趨勢圖：餐飲/二手分列（圖例＋表頭）",
    (await page.locator(".rpt-trend-chart text:has-text('餐飲營收')").count()) > 0 &&
      (await page.locator("th:has-text('餐飲營收')").count()) > 0 &&
      (await page.locator("th:has-text('二手營收')").count()) > 0,
  );
  await shot(page, "report-trends");
  for (const [tab, slug] of [
    ["現金對帳", "report-daily-cash"],
    ["銷售毛利", "report-sales-margin"],
    ["庫存價值", "report-inventory-value"],
    ["寄售應付", "report-consignment-payables"],
  ]) {
    await page.click(`[role="tab"]:has-text("${tab}")`);
    await page.waitForTimeout(900);
    const errored = await page.locator("text=/讀取.*失敗/").count();
    ok(`12) 報表分頁渲染：${tab}`, errored === 0);
    await shot(page, slug);
  }

  // 13) 關帳（實點現金、差異）
  await nav("現金對帳", "/cash");
  await page.waitForSelector('input[name="counted_amount"]', { timeout: 8000 });
  await page.fill('input[name="counted_amount"]', "3000");
  await page.locator('form:has(input[name="counted_amount"]) button:has-text("結帳")').click();
  await page.waitForSelector("text=已結帳", { timeout: 8000 });
  ok("13) 關帳完成（實點 3,000、顯示差異）", true);
  await shot(page, "cash-closed");
} catch (err) {
  ok("流程中斷", false, String(err));
  await shot(page, "FAILURE");
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n結果：${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖目錄：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
