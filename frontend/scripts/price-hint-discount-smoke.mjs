// 收購頁「歷史折數＋六折以上紅字」煙霧（店主 2026-09-24）：
// 用 API 造 3 件同款買斷（點過 5 折、6.5 折，一件沒填參考價），確認行情提示講「歷史折數 5–6.5 折」、
// 最近一次標折數、逐筆紀錄有折數欄；再在收購列點 6 折看到紅字提醒、5 折不出現。
// 需 backend + frontend 已起、已 seed（dev-manager）。執行：node scripts/price-hint-discount-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "price-hint-discount");
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
      ...(method === "POST" ? { "Idempotency-Key": `phd-${RUN}-${Math.random()}` } : {}),
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

  await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [
        { acquisition_cost: "250", listed_price: "500", retail_price: "1000", resale_discount_pct: 50 },
        { acquisition_cost: "330", listed_price: "650", retail_price: "1000", resale_discount_pct: 65 },
        { acquisition_cost: "200", listed_price: "400" },
      ].map((item) => ({
        name: `${BRAND} ${MODEL}`,
        brand_id: brand.id,
        product_model_id: model.id,
        category_id: category.id,
        grade: "A",
        ...item,
      })),
    },
  });
  ok("造出 3 件同款買斷（5 折、6.5 折、一件沒參考價）", true);

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
  await page.getByText(/同型號以前收過 3 件/).waitFor({ timeout: 10_000 });
  const hintText = await hintBox.innerText();
  ok("行情提示講歷史折數 5–6.5 折（2 件有折數）", hintText.includes("歷史折數：5–6.5 折") && hintText.includes("有折數紀錄的 2 件"), hintText.replace(/\n/g, " | "));
  ok("最近一次標折數或不標（最近那件沒填參考價）", !hintText.includes("undefined"));
  await page.getByRole("button", { name: "看各成色行情與最近紀錄" }).click();
  const recent = page.getByRole("table", { name: "最近 5 筆" });
  await recent.waitFor();
  const recentText = await recent.innerText();
  ok("逐筆紀錄有折數欄", recentText.includes("折數") && recentText.includes("6.5 折") && recentText.includes("5 折"), recentText.replace(/\s+/g, " "));
  await hintBox.screenshot({ path: join(SHOTS, "01-hint-discount.png") });

  await page.getByLabel("參考價（原價或目前最低價）").fill("1000");
  await page.getByRole("button", { name: "5折", exact: true }).click();
  ok("5 折不出現新品提醒", (await page.getByText(/可能是新品/).count()) === 0);
  await page.getByRole("button", { name: "6折", exact: true }).click();
  const alert = page.locator(".acq-near-new");
  await alert.waitFor();
  const alertText = await alert.innerText();
  const color = await alert.evaluate((el) => getComputedStyle(el).color);
  ok("6 折出現紅字提醒確認成色", alertText.includes("6 折偏高，可能是新品") && alertText.includes("修改成色"), `${alertText}（${color}）`);
  await page.locator(".acq-quick-pricing").screenshot({ path: join(SHOTS, "02-near-new-warning.png") });

  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  ok("手機寬度不會整頁橫向捲動", !overflow);
  await page.screenshot({ path: join(SHOTS, "03-mobile.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  throw error;
} finally {
  await browser.close();
}
