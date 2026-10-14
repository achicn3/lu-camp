// 報表分開看一般商品與二手（店主 2026-10-14）瀏覽器煙霧：
// 用 API 備資料——同一品牌一件買斷二手（收 600、賣 1500）＋一般商品「USB 充電營燈」賣 2 件 →
// 儀表板：「露營用品總營收」＝二手＋一般商品、「二手營收」只算二手、「一般商品營收」卡附毛利與件數 →
// 經營洞察的暢銷排行：同一品牌分「二手」「一般商品」兩列 → 銷售毛利表也分列。
// 需 backend + frontend 已起、已跑 seed_dev_store / seed_dev_user / seed_dev_purchasing（LANTERN-USB 要有量）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/reports-catalog-split-smoke.mjs
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";
import { openReport } from "./_reports.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS =
  process.env.SMOKE_SHOTS ??
  join(homedir(), "tmp", "lu-camp-shots", "reports-catalog-split");
const RUN = String(Date.now()).slice(-6);
const BRAND = `Snow Peak-${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(
    `${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`,
  );
}

async function call(token, method, path, body, headers = {}) {
  const resp = await fetch(`${API}/api/v1${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  if (!resp.ok) throw new Error(`${method} ${path} → ${resp.status} ${text}`);
  return text ? JSON.parse(text) : null;
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const login = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: "dev-manager",
      password: "dev-test-123456",
    }),
  });
  const token = (await login.json()).access_token;
  const current = await fetch(`${API}/api/v1/cash-sessions/current`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (current.status !== 200 || !(await current.json())) {
    await call(token, "POST", "/cash-sessions/open", { opening_float: "5000" });
  }
  const settings = await call(token, "GET", "/settings");
  if (settings.require_acquisition_affidavit) {
    await call(token, "PATCH", "/settings", {
      require_acquisition_affidavit: false,
    });
  }

  const brand = await call(token, "POST", "/brands", { name: BRAND });
  const seller = await call(token, "POST", "/contacts", {
    name: `報表分列賣家-${RUN}`,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER"],
  });
  const acquisition = await call(
    token,
    "POST",
    "/acquisitions",
    {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [
        {
          name: "二手營燈",
          grade: "A",
          acquisition_cost: "600",
          listed_price: "1500",
          brand_id: brand.id,
        },
      ],
    },
    { "Idempotency-Key": randomUUID() },
  );
  const lantern = await call(
    token,
    "GET",
    "/catalog-products/by-sku/LANTERN-USB",
  );
  await call(token, "PATCH", `/catalog-products/${lantern.id}`, {
    brand_id: brand.id,
  });
  const sale = await call(
    token,
    "POST",
    "/sales",
    {
      lines: [
        { line_type: "SERIALIZED", item_code: acquisition.item_codes[0] },
        { line_type: "CATALOG", catalog_product_id: lantern.id, qty: 2 },
      ],
    },
    { "Idempotency-Key": randomUUID() },
  );
  ok("備資料：同品牌二手 1 件＋一般商品 2 件已售出", sale.lines.length === 2);
  if (settings.require_acquisition_affidavit) {
    await call(token, "PATCH", "/settings", {
      require_acquisition_affidavit: true,
    });
  }

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
  await page.waitForSelector(".rpt-dashboard-cards");

  const num = (t) => Number(t.replace(/[^\d-]/g, ""));
  ok(
    "第一層：當日現金收入／支出",
    (await page.locator("dt", { hasText: "當日現金收入" }).count()) === 1 &&
      (await page.locator("dt", { hasText: "當日現金支出" }).count()) === 1,
  );
  const kind = (name) => page.getByRole("region", { name: `${name}營收` });
  const revenueOf = async (name) =>
    num((await kind(name).locator("dd").first().textContent()) ?? "");
  const secondhandText = (await kind("二手").textContent()) ?? "";
  const catalogText = (await kind("一般商品").textContent()) ?? "";
  ok(
    "第二層：二手、一般商品、餐飲三張卡",
    (await kind("餐飲").count()) === 1 &&
      secondhandText !== "" &&
      catalogText !== "",
  );
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
  }).format(new Date());
  const api = await call(token, "GET", `/reports/daily-summary?date=${today}`);
  const pct = (r) => (r === null ? "—" : `${(Number(r) * 100).toFixed(1)}%`);
  ok(
    "二手卡：營收、毛利率、件數與後端一致",
    (await revenueOf("二手")) === Number(api.secondhand_revenue) &&
      secondhandText.includes(pct(api.secondhand_margin_rate)) &&
      secondhandText.includes(`售出 ${api.secondhand_units_sold} 件`),
    secondhandText,
  );
  ok(
    "一般商品卡：營收、件數與後端一致、提醒沒填進價",
    (await revenueOf("一般商品")) === Number(api.catalog_revenue) &&
      catalogText.includes(`售出 ${api.catalog_units_sold} 件`) &&
      catalogText.includes("沒填進價"),
    catalogText,
  );
  ok(
    "露營用品總營收不在今日營運",
    (await page.getByText("露營用品總營收").count()) === 0,
  );
  await page.screenshot({ path: join(SHOTS, "01-dashboard.png") });

  await openReport(page, "經營洞察");
  const brandRows = page.locator(".rpt-insight-table tbody tr", {
    has: page.locator(`td:text-is("${BRAND}")`),
  });
  await brandRows.first().waitFor({ timeout: 10000 });
  const kinds = await brandRows.locator(".rpt-kind").allTextContents();
  ok(
    "暢銷排行：同一品牌分二手、一般商品兩列",
    kinds.length === 2 && kinds.includes("二手") && kinds.includes("一般商品"),
    kinds.join("、"),
  );
  await brandRows.first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(SHOTS, "02-insights-ranking.png") });

  await openReport(page, "銷售毛利");
  await page.waitForSelector("td:text-is('露營用品總營收')");
  ok(
    "銷售毛利表分列露營用品總營收／二手／一般商品",
    (await page.locator("td:text-is('一般商品營收')").count()) === 1 &&
      (await page.locator("td:text-is('一般商品售出件數')").count()) === 1,
  );
  await page.locator("td:text-is('露營用品總營收')").scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(SHOTS, "03-sales-margin.png") });
  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  ok("流程例外", false, String(error));
  await page
    .screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true })
    .catch(() => {});
} finally {
  await browser.close();
}
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
if (failures.length > 0) process.exitCode = 1;
