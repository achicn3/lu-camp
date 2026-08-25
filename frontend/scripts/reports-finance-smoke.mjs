// E2E smoke for Phase 6 財務報表 UI（核心財務分頁；docs/19、docs/21、docs/32）。
// 以 dev-manager 登入，對真 backend 逐一點開財報分頁，斷言 zh-TW 內容渲染、無錯誤，並截圖。
// 依 docs/20 配方執行；BASE_URL / SMOKE_SHOTS / SEED_USER(_PASSWORD) 由 env 帶入。
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API_OVERRIDE = process.env.SMOKE_API_BASE?.replace(/\/+$/, "") ?? null;
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-shots/reports-finance";
const USER = process.env.SEED_USER ?? "dev-manager";
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });

function assert(cond, msg) {
  if (!cond) throw new Error("ASSERT FAILED: " + msg);
  console.log("  ok: " + msg);
}

async function waitForReport(page) {
  await page.waitForFunction(
    () =>
      !Array.from(document.querySelectorAll(".hint")).some((element) =>
        /載入中/.test(element.textContent ?? ""),
      ),
    undefined,
    { timeout: 30_000 },
  );
}

// 每個財報分頁：tab 文字、截圖檔名、預期出現的關鍵字（任一）。
const FINANCE_TABS = [
  { tab: "今日營運", shot: "01-dashboard", expect: /營業額|認列營收|毛利/ },
  { tab: "趨勢", shot: "02-trends", expect: /趨勢|粒度|認列營收|毛利/ },
  { tab: "現金對帳", shot: "03-daily-cash", expect: /現金|應有|開帳|班別|對帳/ },
  { tab: "銷售毛利", shot: "04-sales-margin", expect: /營業額|毛利|成本/ },
  { tab: "贈品", shot: "05-gifts", expect: /送出|退回|淨額|贈品/ },
  { tab: "庫存價值", shot: "06-inventory-value", expect: /庫存|成本|庫齡|自有/ },
  { tab: "寄售應付", shot: "07-consignment-payables", expect: /寄售|應付|待付|抽成/ },
];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
const page = await ctx.newPage();
if (API_OVERRIDE !== null) {
  await page.route("http://localhost:8000/**", async (route) => {
    const original = new URL(route.request().url());
    const upstream = new URL(original.pathname + original.search, API_OVERRIDE);
    const response = await route.fetch({ url: upstream.toString() });
    await route.fulfill({ response });
  });
}
page.on("console", (m) => {
  if (m.type() === "error") console.log("  [browser console.error]", m.text());
});
let failed = false;
try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"], input#username', USER);
  await page.fill('input[name="password"], input#password', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });
  console.log("logged in, at", page.url());

  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  for (const { tab, shot, expect } of FINANCE_TABS) {
    await page.click(`[role="tab"]:has-text("${tab}"), button:has-text("${tab}")`);
    await waitForReport(page);
    const text = await page.innerText("body");
    assert(expect.test(text), `分頁「${tab}」渲染關鍵字`);
    assert(!/讀取.*失敗|Application error|something went wrong/i.test(text), `分頁「${tab}」無錯誤`);
    // 每個財報分頁都應有匯出按鈕（CSV）。
    const csv = page.locator('button:has-text("CSV")');
    assert((await csv.count()) > 0, `分頁「${tab}」有 CSV 匯出按鈕`);
    if (tab === "銷售毛利") {
      assert(/退回贈品成本/.test(text), "銷售毛利列出退回贈品成本");
      assert(/淨贈品成本/.test(text), "銷售毛利列出淨贈品成本");
    }
    if (tab === "贈品") {
      assert(/期間贈品/.test(text), "贈品摘要表已顯示");
      assert(
        /送出/.test(text) && /退回/.test(text) && /淨額/.test(text),
        "贈品分列送出／退回／淨額",
      );
    }
    await page.screenshot({ path: `${SHOTS}/${shot}.png`, fullPage: true });
    console.log(`  shot: ${shot}.png`);
  }

  // 趨勢圖粒度切換（季）能渲染、不報錯。
  await page.click('[role="tab"]:has-text("趨勢"), button:has-text("趨勢")');
  await waitForReport(page);
  const granularity = page.locator("select").first();
  assert((await granularity.count()) === 1, "趨勢有粒度選單");
  const quarterResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname.endsWith("/api/v1/reports/trends") &&
      url.searchParams.get("granularity") === "quarter";
  }, { timeout: 30_000 });
  await granularity.selectOption("quarter");
  assert((await granularity.inputValue()) === "quarter", "趨勢粒度已切換為季");
  const response = await quarterResponse;
  assert(response.ok(), `季度趨勢 API 回應 ${response.status()}`);
  await waitForReport(page);
  const t = await page.innerText("body");
  assert(
    (await page.locator('[role="alert"].form-error').count()) === 0,
    "季度趨勢沒有報表錯誤訊息",
  );
  assert(!/讀取.*失敗|Application error|Internal Server Error|something went wrong/i.test(t),
    "趨勢切換季粒度不報錯");
  assert((await page.locator('button:has-text("CSV")').count()) > 0, "季度趨勢有 CSV 匯出按鈕");
  await page.screenshot({ path: `${SHOTS}/02-trends-quarter.png`, fullPage: true });

  console.log("\nSMOKE PASS");
} catch (e) {
  failed = true;
  console.error("\nSMOKE FAIL:", e.message);
  try {
    await page.screenshot({ path: `${SHOTS}/99-failure.png`, fullPage: true });
  } catch {}
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
