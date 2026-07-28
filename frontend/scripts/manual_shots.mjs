// 店員操作手冊 — 逐步驟截圖擷取（豐富版）。驅動真 backend(:8010)+frontend(:3010)（大量真實資料），
// 為每個系統拍「手把手」步驟圖：下拉/輸入/錯誤防呆/成品，輸出到 /home/test/tmp/store-manual/screenshots/。
import { existsSync, mkdirSync, renameSync, rmSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.QA_BASE ?? "http://localhost:3010";
const API = process.env.QA_API ?? "http://localhost:8010/api/v1";
const SHOTS = process.env.MANUAL_SHOTS ?? "/home/test/tmp/store-manual/screenshots";
const TAIWAN_TIME_ZONE = "Asia/Taipei";
const RUN_ID = `${process.pid}-${Date.now()}`;
const CAPTURE_DIR = `${SHOTS}.tmp-${RUN_ID}`;
const PREVIOUS_DIR = `${SHOTS}.previous-${RUN_ID}`;
const sectionErrors = [];

function publishCapture() {
  const hadPrevious = existsSync(SHOTS);
  if (hadPrevious) renameSync(SHOTS, PREVIOUS_DIR);
  try {
    renameSync(CAPTURE_DIR, SHOTS);
  } catch (error) {
    if (hadPrevious && !existsSync(SHOTS)) renameSync(PREVIOUS_DIR, SHOTS);
    throw error;
  }
  if (hadPrevious) rmSync(PREVIOUS_DIR, { recursive: true, force: true });
}

const taiwanDateTimeFormatter = new Intl.DateTimeFormat("sv-SE", {
  timeZone: TAIWAN_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function formatTaiwanDateTimeLocal(date) {
  return taiwanDateTimeFormatter.format(date).replace(" ", "T");
}

async function shot(page, name) {
  await page.screenshot({ path: join(CAPTURE_DIR, `${name}.png`), fullPage: true });
  console.log(`  📸 ${name}.png`);
}

// 視窗截圖（非整頁）：用於下拉開啟、表單填寫、錯誤提示等「細節」鏡頭，先把目標捲入視窗。
async function shotV(page, name, selector) {
  if (selector) {
    await page
      .locator(selector)
      .first()
      .scrollIntoViewIfNeeded()
      .catch(() => {});
    await page.waitForTimeout(250);
  }
  await page.screenshot({ path: join(CAPTURE_DIR, `${name}.png`), fullPage: false });
  console.log(`  📸 ${name}.png (viewport)`);
}

async function api(path, { method = "GET", token, body, headers = {} } = {}) {
  const h = { "Content-Type": "application/json", ...headers };
  if (token) h.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, {
    method,
    headers: h,
    body: body ? JSON.stringify(body) : undefined,
  });
  return res;
}

async function getToken() {
  const r = await api("/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  return (await r.json()).access_token;
}

// 備妥：示範賣方（買斷需身分證）＋兩件 POS 要掃的真實二手裝備；確保有開帳。
async function prep(token) {
  const cur = await (await api("/cash-sessions/current", { token })).json();
  if (!cur || cur.status !== "OPEN") {
    await api("/cash-sessions/open", { method: "POST", token, body: { opening_float: "3000" } });
  }
  let sellerId;
  const sc = await api("/contacts", {
    method: "POST",
    token,
    body: { name: "手冊示範賣方", phone: "0988123456", national_id: "A200000003", roles: ["SELLER"] },
  });
  if (sc.status < 300) sellerId = (await sc.json()).id;
  else {
    // 已存在（同店電話唯一）→ 以搜尋取回既有賣方 id。
    const found = await (await api("/contacts?q=0988123456", { token })).json();
    sellerId = Array.isArray(found) && found.length ? found[0].id : undefined;
  }
  // 優先撿現有 IN_STOCK 自有序號品（可重複跑、不每次新建）；不足才補收購。
  const codes = [];
  const inStock = await (
    await api("/serialized-items?status=IN_STOCK&ownership=OWNED&limit=20", { token })
  ).json();
  for (const it of inStock) {
    if (it.ownership_type === "OWNED" && codes.length < 2) codes.push(it.item_code);
  }
  while (codes.length < 2) {
    const it = {
      name: codes.length === 0 ? "Snow Peak 焚火台 L" : "Coleman 氣化燈 286A",
      listed_price: "5800",
      acquisition_cost: "3000",
      grade: "A",
    };
    const r = await api("/acquisitions", {
      method: "POST",
      token,
      headers: { "Idempotency-Key": `manual-${Date.now()}-${Math.random()}` },
      body: { type: "BUYOUT", contact_id: sellerId, payout_method: "CASH", items: [it] },
    });
    if (r.status < 300) codes.push((await r.json()).item_codes[0]);
    else break;
  }
  console.log("  備妥 POS 商品:", codes);
  return codes;
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  // router.replace 為前端轉場，等首頁標題出現即可（比 waitForURL 對 App Router 更穩）。
  await page.getByText("門市作業", { exact: false }).first().waitFor({ timeout: 15000 });
}

async function scan(page, code) {
  await page.fill('input[name="code"]', code);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(700);
}

async function go(page, path) {
  await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
}

const T = (page, t) => page.waitForTimeout(t);

async function section(label, fn) {
  console.log(`\n— ${label} —`);
  try {
    await fn();
  } catch (e) {
    const message = String(e).slice(0, 160);
    sectionErrors.push(`${label}: ${message}`);
    console.log(`  ⚠️ ${label} 部分失敗（已截到的圖仍保留）：${message}`);
  }
}

async function main() {
  const token = await getToken();
  const codes = await prep(token);
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1366, height: 1000 },
    timezoneId: TAIWAN_TIME_ZONE,
  });
  await login(page);
  mkdirSync(CAPTURE_DIR, { recursive: true });

  // ── 0 登入 / 首頁 ──
  await section("登入 / 首頁", async () => {
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
    await shot(page, "01-login");
    await login(page);
    await shot(page, "02-dashboard");
  });

  // ── 1 POS ──
  await section("POS 結帳", async () => {
    await go(page, "/pos");
    await shot(page, "10-pos-empty");
    if (codes[0]) await scan(page, codes[0]);
    if (codes[1]) await scan(page, codes[1]);
    await shot(page, "11-pos-cart");
    const tile = page.locator(".pos-menu-tile").first();
    if (await tile.count()) {
      await tile.click();
      const dlg = page.locator('[role="dialog"]');
      await dlg.waitFor({ state: "visible", timeout: 4000 });
      await shot(page, "12-pos-menu-qty");
      await dlg.getByRole("button", { name: "加入購物車" }).click();
      await T(page, 500);
    }
    await shot(page, "13-pos-cart-with-menu");
    const ms = page.locator(".pos-member-search input");
    if (await ms.count()) {
      await ms.fill("0910000000");
      await page.click('button:has-text("查詢會員")');
      await T(page, 800);
      const r = page.locator(".pos-member-results button").first();
      if (await r.count()) await r.click();
      await T(page, 400);
    }
    await shot(page, "14-pos-member");
    const cashMode = page.locator(".pos-tender-mode", { hasText: "現金" });
    if (await cashMode.count()) await cashMode.first().click();
    await page.click('button:has-text("結帳")').catch(() => {});
    await T(page, 800);
    await shot(page, "15-pos-print-dialog");
    const dlg2 = page.locator('[role="dialog"]');
    if (await dlg2.count())
      await dlg2
        .locator("button")
        .filter({ hasText: /完成|不/ })
        .last()
        .click()
        .catch(() => {});
    await T(page, 800);
    await shot(page, "16-pos-done");
  });

  // ── 2 收購（重點：下拉、鑑價列、撥款、防呆、送出）──
  await section("收購：空表單 + 送出防呆", async () => {
    await go(page, "/acquisition");
    await shot(page, "20-acq-form");
    // 空表單直接送出 → 顯示防呆錯誤清單
    await page.click('button:has-text("送出收購")').catch(() => {});
    await T(page, 500);
    await shot(page, "21-acq-validation");
  });

  await section("收購：品牌下拉（查無即建）", async () => {
    await go(page, "/acquisition");
    const brand = page.locator('.combo:has(label:text-is("品牌")) .combo-input').first();
    await brand.click();
    await brand.fill("Snow");
    await T(page, 700);
    await shotV(page, "22-acq-brand-combo", ".acq-row");
  });

  await section("收購：新賣方身分證防呆", async () => {
    await go(page, "/acquisition");
    await page.click('button:has-text("建立新賣方")').catch(() => {});
    await T(page, 300);
    await page.fill('input[aria-label="姓名"]', "陳大明");
    await page.fill('input[aria-label="手機"]', "0966555444");
    await page.fill('input[aria-label="身分證字號"]', "A123456788"); // 檢核碼錯
    await page.click('button:has-text("建立並選取")').catch(() => {});
    await T(page, 500);
    await shotV(page, "23-acq-seller-id-error", ".acq-create-seller");
  });

  await section("收購：填鑑價列 + 撥款方式 + 購物金提醒 + 送出成功", async () => {
    await go(page, "/acquisition");
    // 選既有賣方（已建檔身分證）
    const search = page.locator('input[aria-label="賣方搜尋"]');
    await search.fill("0988123456");
    await T(page, 800);
    const opt = page.locator(".acq-results button").first();
    if (await opt.count()) await opt.click();
    await T(page, 400);
    // 填鑑價列
    await page.fill('input[aria-label="品名"]', "MSR Hubba Hubba 帳篷");
    const category = page.getByLabel("分類", { exact: true });
    await category.fill("帳篷");
    await page.locator(".acq-row .combo-menu button").first().waitFor({ timeout: 5000 });
    const categoryOption = page
      .locator(".acq-row .combo-menu .combo-option")
      .filter({ hasText: "帳篷" })
      .first();
    if (await categoryOption.count()) await categoryOption.click();
    else await page.locator('.acq-row .combo-create:has-text("帳篷")').click();
    await page.selectOption('.acq-row select', "A").catch(() => {});
    await page.fill('input[aria-label="估計轉售價"]', "9000");
    await page.fill('input[aria-label="收購價"]', "5000");
    await page.fill('input[aria-label="上架售價"]', "8800");
    await T(page, 400);
    await shot(page, "24-acq-row-filled");
    // 撥款方式：購物金（賣方非會員）→ 提醒
    const credit = page.locator(".acq-payout-mode", { hasText: "購物金" });
    if (await credit.count()) await credit.first().locator("input").check().catch(() => {});
    await T(page, 400);
    await shot(page, "25-acq-payout-credit-warn");
    // 切回現金 → 送出
    const cash = page.locator(".acq-payout-mode", { hasText: "現金" });
    if (await cash.count()) await cash.first().locator("input").check().catch(() => {});
    await T(page, 300);
    await shot(page, "26-acq-payout-cash");
    await page.click('button:has-text("送出收購")');
    await page.locator(".acq-result").waitFor({ timeout: 15000 });
    await shot(page, "27-acq-success");
  });

  // ── 3 寄售（重點：新的手機查找 + 付款確認）──
  await section("寄售付款", async () => {
    await go(page, "/consignment");
    await shotV(page, "30-consignment", ".settle-head");
    // 新功能：以寄售人手機查找
    const ph = page.locator('input[aria-label="以寄售人手機查找"]');
    if (await ph.count()) {
      await ph.fill("0981000032");
      await page.click('button:has-text("查找")');
      await T(page, 800);
      await shotV(page, "31-consign-phone-search", ".settle-head");
    }
    // 付款確認對話框（不真的付，截完取消）
    const payBtn = page.locator('button:has-text("付款")').first();
    if (await payBtn.count()) {
      await payBtn.click();
      await T(page, 500);
      await shot(page, "32-consign-pay-confirm");
      await page.locator('[role="dialog"] button:has-text("取消")').click().catch(() => {});
    }
  });

  // ── 4 庫存 ──
  await section("庫存", async () => {
    await go(page, "/inventory");
    await T(page, 600);
    await shot(page, "40-inventory");
    const bulkTab = page.locator(".inv-tab", { hasText: "散裝批" });
    if (await bulkTab.count()) {
      await bulkTab.first().click();
      await T(page, 700);
      await shot(page, "41-inventory-bulk");
    }
  });

  // ── 5 採購（重點：新的供應商下拉 + 明細 + 防呆 + 收貨）──
  await section("採購補貨", async () => {
    await go(page, "/purchasing");
    // 建立採購單預設收合，先展開才能截圖與操作。
    await page.locator('button.pur-create-toggle:has-text("＋ 建立採購單")').click();
    await T(page, 400);
    await shotV(page, "50-purchasing", ".pur-lowstock");
    // 新功能：供應商「查無即建」下拉
    const sup = page.locator('.combo:has(label:text-is("供應商")) .combo-input').first();
    if (await sup.count()) {
      await sup.click();
      await sup.fill("山");
      await T(page, 600);
      await shotV(page, "51-pur-supplier-combo", ".pur-create");
      const supOpt = page.locator(".combo-option").first();
      if (await supOpt.count()) await supOpt.click();
      await T(page, 300);
    }
    // 搜尋一般商品 → 下拉 → 加入明細
    const ps = page.locator('input[aria-label="搜尋一般商品"]');
    if (await ps.count()) {
      await ps.fill("瓦斯");
      await T(page, 800);
      await shotV(page, "52-pur-product-search", ".pur-create");
      const addBtn = page.locator(".pur-search-results button").first();
      if (await addBtn.count()) await addBtn.click();
      await T(page, 400);
      // 填數量/單價
      const qty = page.locator(".pur-qty").first();
      const cost = page.locator(".pur-cost").first();
      if (await qty.count()) await qty.fill("12");
      if (await cost.count()) await cost.fill("60");
      await T(page, 300);
      await shotV(page, "53-pur-lines", ".pur-create");
      // 防呆：數量設 0
      if (await qty.count()) {
        await qty.fill("0");
        await T(page, 300);
        await shotV(page, "54-pur-qty-error", ".pur-create");
        await qty.fill("12");
      }
    }
    // 收貨確認對話框（截完取消）
    const recv = page.locator('button:has-text("收貨入庫")').first();
    if (await recv.count()) {
      await recv.click();
      await T(page, 500);
      await shot(page, "55-pur-receive-confirm");
      await page.locator('[role="dialog"] button:has-text("取消")').click().catch(() => {});
    }
    // 採購單清單：狀態篩選 chips + 分頁（不再一次倒出全部）
    if (await page.locator(".pur-orders").count()) {
      await page.locator('.pur-orders .chip:has-text("待收貨")').click().catch(() => {});
      await T(page, 600);
      await shotV(page, "56-pur-orders-filter", ".pur-orders");
    }
    // 供應商分頁：搜尋 + 分頁
    await page.locator('.settle-tabs .chip:has-text("供應商")').click().catch(() => {});
    await T(page, 600);
    await shotV(page, "57-pur-suppliers", ".pur-supplier-list");
  });

  // ── 6 盤點 ──
  await section("盤點", async () => {
    await go(page, "/stocktake");
    await shot(page, "60-stocktake");
  });

  // ── 7 門市活動（重點：建立表單 + 餐飲不折提示 + 折扣防呆）──
  await section("門市活動", async () => {
    await go(page, "/campaigns");
    await shot(page, "70-campaigns");
    // 建立表單：填名稱、折扣、時間，露出「餐飲不參與折扣」提示
    await page.fill('input[placeholder="例如：開幕九折"]', "週年慶 88 折").catch(() => {});
    await page.fill('input[placeholder="10 = 打九折"]', "12").catch(() => {});
    const campaignTimes = page.locator('.campaign-form input[type="datetime-local"]');
    const now = Date.now();
    const campaignStart = formatTaiwanDateTimeLocal(new Date(now + 60 * 60 * 1000));
    const campaignEnd = formatTaiwanDateTimeLocal(new Date(now + 25 * 60 * 60 * 1000));
    await campaignTimes.nth(0).fill(campaignStart);
    await campaignTimes.nth(1).fill(campaignEnd);
    await T(page, 300);
    await shotV(page, "71-campaign-create", ".campaign-form");
    // 防呆：折扣填 150（超範圍）→ 送出 → 錯誤
    await page.fill('input[placeholder="10 = 打九折"]', "150").catch(() => {});
    await page.click('button:has-text("建立活動")').catch(() => {});
    await T(page, 500);
    await shotV(page, "72-campaign-discount-error", ".campaign-form");
  });

  // ── 8 餐飲菜單 ──
  await section("餐飲菜單", async () => {
    await go(page, "/menu");
    await shot(page, "80-menu");
    // 新增品項表單 + 售價防呆（0）
    await page.fill('input[placeholder="例如：手沖-耶加雪菲"]', "冰滴咖啡").catch(() => {});
    await page.fill('input[placeholder="180"]', "0").catch(() => {});
    await page.click('button:has-text("新增品項")').catch(() => {});
    await T(page, 400);
    await shotV(page, "81-menu-form-error", "form.card");
  });

  // ── 9 現金對帳 ──
  await section("現金對帳", async () => {
    await go(page, "/cash");
    await shot(page, "90-cash");
  });

  // ── 10 會員 / 賣方（查找 tab + 身分證精確 + 所有會員清單&購物金 + 建檔防呆）──
  await section("會員 / 賣方", async () => {
    await go(page, "/contacts");
    await shotV(page, "a0-contacts-search", ".member-search"); // 預設不直接列出全部
    // 姓名/電話搜尋 → 結果
    await page.fill('input[aria-label="姓名或電話搜尋"]', "林").catch(() => {});
    await page.click('button:has-text("搜尋")').catch(() => {});
    await T(page, 800);
    await shotV(page, "a1-contacts-search-result", ".member-search");
    // 身分證「精確」模式 → 提示
    await page.click('button:has-text("身分證字號（精確）")').catch(() => {});
    await T(page, 300);
    await shotV(page, "a2-contacts-id-mode", ".member-search");
    // 所有會員 tab → 清單 + 購物金
    await page.click('button:has-text("所有會員")').catch(() => {});
    await T(page, 800);
    await shotV(page, "a3-contacts-all", ".member-allsearch");
    // 建檔 + 防呆
    await page.click('button:has-text("查找會員")').catch(() => {});
    await T(page, 300);
    await page.fill('input[name="name"]', "王小明").catch(() => {});
    await page.fill('input[name="phone"]', "0922333444").catch(() => {});
    const contactCreateForm = 'form.card:has(h2:text-is("新增會員/賣方"))';
    await shotV(page, "a4-contacts-new", contactCreateForm);
    await page.fill('input[name="national_id"]', "A123456788").catch(() => {});
    await page.click('button:has-text("建檔")').catch(() => {});
    await T(page, 400);
    await shotV(page, "a5-contacts-id-error", contactCreateForm);
  });

  // ── 11 報表 ──
  await section("報表", async () => {
    await go(page, "/reports");
    await T(page, 500);
    await shot(page, "b0-reports-daily");
    await page.click('button:has-text("趨勢"), a:has-text("趨勢")').catch(() => {});
    await T(page, 1200);
    await shot(page, "b1-reports-trends");
    await page.click('button:has-text("銷售毛利"), a:has-text("銷售毛利")').catch(() => {});
    await T(page, 1000);
    await shot(page, "b2-reports-margin");
  });

  // ── 12 設定 ──
  await section("設定", async () => {
    await go(page, "/settings");
    await shot(page, "c0-settings");
  });

  await browser.close();
  if (sectionErrors.length > 0) {
    throw new Error(`手冊截圖有 ${sectionErrors.length} 個區段失敗：${sectionErrors.join("；")}`);
  }
  publishCapture();
  console.log("\n=== 手冊截圖完成 ===");
}

main().catch((e) => {
  console.error("FATAL", e);
  process.exit(1);
});
