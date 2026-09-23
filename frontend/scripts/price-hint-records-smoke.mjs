// 收購行情提示「一般行情＋最近 5 筆＋整年紀錄」煙霧（2026-09-23）：
// 用 API 造 26 件同款買斷（含一件特價 300、一件標價填錯 9000），確認畫面最上面是不受極端值
// 影響的一般行情、展開看得到最近 5 筆、並能翻看全部 26 筆。
// 需 backend + frontend 已起、已 seed（dev-manager）。執行：node scripts/price-hint-records-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "price-hint-records");
const RUN = String(Date.now()).slice(-6);
const BRAND = `蠻牛-${RUN}`;
const MODEL = `營釘 20cm-${RUN}`;
const CATEGORY = `露營配件-${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(path, { method = "GET", token, body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(method === "POST" ? { "Idempotency-Key": `phr-${RUN}-${Math.random()}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  }
  return response.status === 204 ? null : response.json();
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current === null) {
    await apiJson("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "50000" },
    });
  }

  const brand = await apiJson("/api/v1/brands", { method: "POST", token, body: { name: BRAND } });
  const model = await apiJson("/api/v1/product-models", {
    method: "POST",
    token,
    body: { brand_id: brand.id, name: MODEL },
  });
  const category = await apiJson("/api/v1/categories", {
    method: "POST",
    token,
    body: { name: CATEGORY },
  });
  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body: {
      name: `王賣家-${RUN}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
    },
  });

  // 24 件一般行情（收 1100–1400、上架 2300–2900）＋ 特價 300／1680 ＋ 填錯 2400／9000。
  const items = [];
  for (let i = 0; i < 24; i += 1) {
    items.push({ acquisition_cost: String(1100 + (i % 4) * 100), listed_price: String(2300 + (i % 4) * 200) });
  }
  items.push({ acquisition_cost: "300", listed_price: "1680" });
  items.push({ acquisition_cost: "2400", listed_price: "9000" });
  await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: items.map((item) => ({
        name: `${BRAND} ${MODEL}`,
        brand_id: brand.id,
        product_model_id: model.id,
        category_id: category.id,
        grade: "B",
        ...item,
      })),
    },
  });
  ok("造出 26 件同款買斷（含兩件極端值）", true);

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.waitForSelector('[role="tab"]:has-text("買斷")');

  await page.getByLabel("品牌", { exact: true }).click();
  await page.getByLabel("品牌", { exact: true }).fill(BRAND);
  await page.getByRole("option", { name: BRAND, exact: true }).click();
  await page.getByLabel("型號", { exact: true }).click();
  await page.getByLabel("型號", { exact: true }).fill(MODEL);
  await page.getByRole("option", { name: MODEL, exact: true }).click();

  const hintBox = page.locator(".price-hint");
  await page.getByText(/同型號以前收過 26 件/).waitFor({ timeout: 10_000 });
  const ranges = await page.locator(".price-hint-ranges").innerText();
  ok(
    "最上面是一般行情，不被特價 300／填錯 9000 拉開",
    ranges.includes("一般收購價") && ranges.includes("1,100–1,400") && ranges.includes("2,300–2,900"),
    ranges.replace(/\n/g, " | "),
  );
  const hintText = await hintBox.innerText();
  ok(
    "最低～最高退成一行參考",
    hintText.includes("最低～最高") && hintText.includes("300–2,400") && hintText.includes("1,680–9,000"),
  );
  await hintBox.screenshot({ path: join(SHOTS, "01-typical.png") });

  await page.getByRole("button", { name: "看各成色行情與最近紀錄" }).click();
  const recent = page.getByRole("table", { name: "最近 5 筆" });
  await recent.waitFor();
  ok("展開看得到最近 5 筆", (await recent.locator("tbody tr").count()) === 5);
  await hintBox.screenshot({ path: join(SHOTS, "02-recent-5.png") });

  await page.getByRole("button", { name: "看近一年全部 26 筆收購紀錄" }).click();
  const all = page.getByRole("table", { name: "全部收購紀錄" });
  await all.waitFor();
  ok("全部紀錄第一頁 20 筆", (await all.locator("tbody tr").count()) === 20);
  ok("顯示第 1 / 2 頁", await page.getByText("第 1 / 2 頁").isVisible());
  await page.getByRole("button", { name: "下一頁" }).click();
  await page.getByText("第 2 / 2 頁").waitFor();
  ok("第二頁剩 6 筆", (await all.locator("tbody tr").count()) === 6);
  await hintBox.screenshot({ path: join(SHOTS, "03-all-page-2.png") });

  await page.getByRole("button", { name: "收起全部紀錄" }).click();
  ok("可收起全部紀錄", (await page.getByRole("table", { name: "全部收購紀錄" }).count()) === 0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "看近一年全部 26 筆收購紀錄" }).click();
  await page.getByRole("table", { name: "全部收購紀錄" }).waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  ok("手機寬度不會整頁橫向捲動", !overflow);
  await hintBox.screenshot({ path: join(SHOTS, "04-mobile.png") });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  throw error;
} finally {
  await browser.close();
}
