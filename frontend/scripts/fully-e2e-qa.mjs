// Layer C-2 + Layer D — 全系統 UI 走查 + 前端缺陷偵測 + 雙瀏覽器併發。
// 對「真 backend(:8010, lucamp_e2e) + 真 frontend(:3010)」逐路由走查：
//   - 蒐集 pageerror（未捕捉例外）與 console.error（D-1）
//   - 每個系統全頁截圖到 ~/test/tmp/fully-e2e-test/<情境>/（使用者指定路徑）
//   - 對高風險表單做防呆試錯（D-2）
//   - 雙瀏覽器（PC-A=POS、PC-B=收購）同開、共用同一現金班別（C-2）
// 輸出 JSON 摘要供 FINDINGS.md 彙整。
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.QA_BASE ?? "http://localhost:3010";
const API = process.env.QA_API ?? "http://127.0.0.1:8010";

// Node 端直查後端（防呆斷言用）：以 dev 帳號取 token 查 contacts 筆數；失敗回 -1（略過該斷言）
async function countContactsByName(q) {
  try {
    const login = await fetch(`${API}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "dev-manager", password: process.env.SEED_USER_PASSWORD ?? "dev-test-123456" }),
    });
    if (!login.ok) return -1;
    const { access_token } = await login.json();
    const res = await fetch(`${API}/api/v1/contacts?q=${encodeURIComponent(q)}&limit=5`, {
      headers: { Authorization: `Bearer ${access_token}` },
    });
    if (!res.ok) return -1;
    const data = await res.json();
    return Array.isArray(data) ? data.length : (data.items?.length ?? -1);
  } catch {
    return -1;
  }
}
const ROOT = process.env.QA_SHOTS ?? "/home/test/test/tmp/fully-e2e-test";
const USER = "dev-manager";
const PASS = "dev-test-123456";

const findings = [];
const jsErrors = []; // {route, kind, text}
const jargonHits = []; // {route, term, context}

// 給「無軟體背景店員」看的畫面不該出現的專業術語/未中文化字串。
// 刻意避開正常會出現的品牌大寫（MSR/DOD/SOTO…）、商品碼、CSV/Excel 按鈕。
const JARGON = [
  "IN_STOCK", "SOLD_OUT", "NOT_ISSUED", "STORE_CREDIT", "BULK_LOT", "SERIALIZED",
  "OWNED", "CONSIGNMENT", "PENDING", "CANCELLED", "ENDED", "DRAFT",
  "idempotency", "store_id", "session_id", "null", "undefined", "NaN",
];
// 這些英文狀態詞以「獨立單字」偵測（避免誤判品牌/商品名內的字母）。
const JARGON_WORD = ["OPEN", "CLOSED", "ACTIVE", "PAID", "SOLD", "VOID", "MANAGER", "STAFF"];

function scanJargon(route, text) {
  for (const term of JARGON) {
    const idx = text.indexOf(term);
    if (idx >= 0) jargonHits.push({ route, term, context: text.slice(Math.max(0, idx - 20), idx + term.length + 20).replace(/\s+/g, " ") });
  }
  for (const term of JARGON_WORD) {
    const re = new RegExp(`(^|[^A-Za-z])${term}([^A-Za-z]|$)`);
    const m = re.exec(text);
    if (m) {
      const idx = m.index;
      jargonHits.push({ route, term, context: text.slice(Math.max(0, idx - 20), idx + term.length + 22).replace(/\s+/g, " ") });
    }
  }
}
function note(scenario, severity, title, detail) {
  findings.push({ scenario, severity, title, detail });
  console.log(`  [${severity}] ${scenario} :: ${title}${detail ? " — " + detail : ""}`);
}

function attachErrorCollectors(page, label) {
  page.on("pageerror", (err) => {
    jsErrors.push({ route: label(), kind: "pageerror", text: String(err) });
  });
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      const t = msg.text();
      // 過濾掉 favicon/網路 404 雜訊以外的真正錯誤
      jsErrors.push({ route: label(), kind: "console.error", text: t.slice(0, 300) });
    }
  });
}

async function shot(page, folder, slug) {
  const dir = join(ROOT, folder);
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${slug}.png`);
  await page.screenshot({ path, fullPage: true });
  console.log(`  📸 ${folder}/${slug}.png`);
  return path;
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USER);
  await page.fill('input[name="password"]', PASS);
  await page.click('button:has-text("登入")');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });
}

// 逐路由走查：navigate→settle→screenshot→錯誤歸戶。
const ROUTES = [
  ["/", "00-auth-dashboard", "01-dashboard"],
  ["/settings", "00-auth-dashboard", "02-settings"],
  ["/acquisition", "01-acquisition", "01-acquisition"],
  ["/inventory", "02-inventory", "01-inventory-list"],
  ["/consignment", "03-consignment", "01-consignment"],
  ["/pos", "04-pos-sales", "01-pos-empty"],
  ["/menu", "05-menu-fnb", "01-menu-manage"],
  ["/purchasing", "06-purchasing", "01-purchasing"],
  ["/stocktake", "07-stocktake", "01-stocktake"],
  ["/campaigns", "08-campaigns", "01-campaigns"],
  ["/cash", "10-cash-reconcile", "01-cash"],
  ["/reports", "11-reports", "01-reports"],
  ["/contacts", "13-ui-defects", "01-contacts-list"],
];

async function sweepRoutes(page) {
  for (const [route, folder, slug] of ROUTES) {
    const before = jsErrors.length;
    try {
      const resp = await page.goto(`${BASE}${route}`, {
        waitUntil: "networkidle",
        timeout: 20000,
      });
      // goto 對 404/500 一樣正常 resolve——靜態錯誤頁不丟 console error 也能綠燈出場，
      // 必須驗 HTTP 狀態（Codex 第三輪 P1）。
      if (resp && resp.status() >= 400) {
        note(folder, "缺陷", `路由 ${route} 回 HTTP ${resp.status()}`, "非 2xx/3xx 頁面");
      }
      await page.waitForTimeout(700); // 等資料載入/渲染
    } catch (e) {
      note(folder, "缺陷", `路由 ${route} 載入失敗`, String(e).slice(0, 160));
    }
    await shot(page, folder, slug);
    try {
      const txt = await page.locator("body").innerText();
      scanJargon(route, txt);
    } catch { /* ignore */ }
    const newErrs = jsErrors.slice(before).filter((e) => e.route === route);
    const pe = newErrs.filter((e) => e.kind === "pageerror");
    if (pe.length) {
      note(folder, "系統壞", `路由 ${route} 有未捕捉 JS 例外`, pe.map((e) => e.text).join(" | ").slice(0, 200));
    }
  }
}

// D-2：會員建檔防呆（身分證檢核 / 必填）——在 /contacts 頁試錯。
async function checkContactValidation(page) {
  const folder = "13-ui-defects";
  try {
    await page.goto(`${BASE}/contacts`, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    // 嘗試開「新增會員」表單（不同實作：按鈕文字可能為「新增會員/建立會員/新增」）
    const addBtn = page.locator('button:has-text("新增"), button:has-text("建立會員"), a:has-text("新增會員")').first();
    if (await addBtn.count()) {
      await addBtn.click().catch(() => {});
      await page.waitForTimeout(400);
    }
    const nameInput = page.locator('input[name="name"]').first();
    if (await nameInput.count()) {
      // 一切 selector 收斂到「含 name 輸入框的那張建檔表單」內：頁面另有搜尋表單，
      // 未 scope 會按到搜尋鈕、且 body 全文 regex 永遠命中欄位標籤→假通過（Codex P2）。
      const form = page.locator("form", { has: page.locator('input[name="name"]') }).first();
      await form.locator('input[name="name"]').fill("QA防呆測試");
      await form.locator('input[name="phone"]').fill("0987000111");
      const nid = form.locator('input[name="national_id"]').first();
      if (await nid.count()) await nid.fill("A123456788"); // 檢核碼錯
      await shot(page, folder, "10-contact-invalid-nid-filled");
      const beforeCount = await countContactsByName("QA防呆測試");
      await form.locator('button[type="submit"]').first().click().catch(() => {});
      await page.waitForTimeout(800);
      await shot(page, folder, "11-contact-invalid-nid-result");
      // 斷言 1：建檔表單內出現錯誤提示（scope 在表單，不吃頁面靜態文字）
      const formText = await form.innerText().catch(() => "");
      const blocked = /檢核|格式不正確|無效|錯誤/.test(formText);
      // 斷言 2：資料未新增（以後端 API 實查，防「畫面沒動但已寫入」）
      const afterCount = await countContactsByName("QA防呆測試");
      const notCreated = beforeCount >= 0 && afterCount === beforeCount;
      const pass = blocked && (notCreated || beforeCount < 0);
      note(folder, pass ? "資訊" : "缺陷",
        pass ? "會員建檔身分證防呆：表單內擋下且未寫入" : "會員建檔身分證防呆：未確認擋下（需人工複核）",
        `表單錯誤提示=${blocked}；API 筆數 前=${beforeCount} 後=${afterCount}`);
      if (!pass) process.exitCode = 1;
    } else {
      note(folder, "資訊", "會員建檔表單未自動開啟", "找不到 name 輸入框，略過此防呆檢查（非缺陷）");
    }
  } catch (e) {
    // 探針自身壞掉（selector 失效等）不得默默出綠燈——必測項未執行＝缺陷（Codex 第三輪 P1）
    note(folder, "缺陷", "會員防呆檢查例外（必測項未完成）", String(e).slice(0, 160));
  }
}

// 報表/庫存大表渲染（D-5）：120 天資料後是否正常渲染、是否有 JS 錯誤。
async function checkBigTables(page) {
  for (const [route, folder, slug] of [
    ["/reports", "11-reports", "02-reports-after-load"],
    ["/inventory", "02-inventory", "02-inventory-after-load"],
  ]) {
    const before = jsErrors.length;
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" }).catch(() => {});
    await page.waitForTimeout(900);
    await shot(page, folder, slug);
    const pe = jsErrors.slice(before).filter((e) => e.kind === "pageerror");
    note(folder, pe.length ? "系統壞" : "資訊",
      pe.length ? `大表渲染有 JS 例外` : `大表渲染正常（120 天資料）`,
      pe.map((e) => e.text).join(" | ").slice(0, 160));
  }
}

// C-2：雙瀏覽器併發——PC-A 開 POS、PC-B 開收購，共用同一現金班別。
async function dualBrowser(browser) {
  const folder = "12-multiPC-concurrency";
  const ctxA = await browser.newContext({ viewport: { width: 1366, height: 1000 } });
  const ctxB = await browser.newContext({ viewport: { width: 1366, height: 1000 } });
  const pageA = await ctxA.newPage();
  const pageB = await ctxB.newPage();
  attachErrorCollectors(pageA, () => "PC-A:" + pageA.url());
  attachErrorCollectors(pageB, () => "PC-B:" + pageB.url());
  await login(pageA);
  await login(pageB);
  await pageA.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await pageB.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await pageA.waitForTimeout(600);
  await pageB.waitForTimeout(600);
  await shot(pageA, folder, "01-PC-A-pos");
  await shot(pageB, folder, "02-PC-B-acquisition");
  // 兩台同時看 /cash → 應為同一個 OPEN 班別（單店單抽屜）
  await pageA.goto(`${BASE}/cash`, { waitUntil: "networkidle" });
  await pageB.goto(`${BASE}/cash`, { waitUntil: "networkidle" });
  await pageA.waitForTimeout(500);
  await pageB.waitForTimeout(500);
  await shot(pageA, folder, "03-PC-A-cash-shared-session");
  await shot(pageB, folder, "04-PC-B-cash-shared-session");
  note(folder, "資訊", "雙瀏覽器同開 POS/收購並共用現金班別", "截圖佐證單店單抽屜共享；API 層併發已由 Layer C-1 P1-P8 驗證");
  await ctxA.close();
  await ctxB.close();
}

async function main() {
  mkdirSync(ROOT, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 1000 } });
  const page = await ctx.newPage();
  attachErrorCollectors(page, () => new URL(page.url()).pathname);

  console.log("=== 登入 + 路由走查 ===");
  // login 頁截圖（00）
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await shot(page, "00-auth-dashboard", "00-login");
  await login(page);
  await sweepRoutes(page);

  console.log("=== Layer D 表單防呆 / 大表渲染 ===");
  await checkContactValidation(page);
  await checkBigTables(page);

  await ctx.close();

  console.log("=== Layer C-2 雙瀏覽器併發 ===");
  await dualBrowser(browser);

  await browser.close();

  // 彙整：把 pageerror 升級為 finding
  const pageerrors = jsErrors.filter((e) => e.kind === "pageerror");
  const consoleErrors = jsErrors.filter((e) => e.kind === "console.error");
  // 去重 jargon（同 term+context 只留一筆）
  const seen = new Set();
  const jargonUnique = jargonHits.filter((h) => {
    const k = `${h.route}|${h.term}|${h.context}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  const summary = {
    routes_swept: ROUTES.length,
    pageerror_count: pageerrors.length,
    console_error_count: consoleErrors.length,
    jargon_count: jargonUnique.length,
    jargon: jargonUnique,
    pageerrors,
    console_errors_sample: consoleErrors.slice(0, 30),
    findings,
  };
  const out = join(ROOT, "ui-sweep-summary.json");
  writeFileSync(out, JSON.stringify(summary, null, 2));
  console.log(`\n=== UI 走查完成 ===`);
  console.log(`  路由 ${ROUTES.length}、pageerror ${pageerrors.length}、console.error ${consoleErrors.length}、jargon ${jargonUnique.length}`);
  console.log(`  摘要：${out}`);
  if (jargonUnique.length) {
    console.log("  ⚠️ 可能的專業術語/未中文化字串：");
    for (const h of jargonUnique) console.log(`    - [${h.route}] ${h.term}：…${h.context}…`);
  }
  if (pageerrors.length) {
    console.log("  ⚠️ 未捕捉 JS 例外：");
    for (const e of pageerrors.slice(0, 10)) console.log(`    - ${e.route}: ${e.text.slice(0, 160)}`);
  }
  // fail-closed（Codex 第二輪 P1）：收集到 JS 例外/console.error/缺陷級 finding，
  // 結束碼必須非零——否則「有壞的 run」也能綠燈出場。
  const defectFindings = findings.filter((f) => f.severity === "缺陷" || f.severity === "系統壞");
  if (pageerrors.length || consoleErrors.length || defectFindings.length) {
    console.log(
      `  ❌ fail-closed：pageerror=${pageerrors.length} console.error=${consoleErrors.length} 缺陷=${defectFindings.length}`,
    );
    process.exitCode = 1;
  }
}

main().catch((e) => {
  console.error("FATAL", e);
  process.exit(1);
});
