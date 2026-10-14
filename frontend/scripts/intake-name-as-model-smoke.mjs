// 排隊收購：待整理上架型號旁的「品名」＋估價打型號找得到（店主 2026-10-15）。
// 付款後的一批（簡稱「焚火台 M…」）→ 整理上架頁：建品牌 → 按型號旁的「品名」→ 型號＝簡稱、品名跟著變
// → 選分類、上架（標籤品名＝型號）→ 另一位客人估價「詳細」打型號的一部分 →
// 建議清單寫「上架為 品牌 型號」→ 點選、帶入 → 存檔後品牌與型號都帶到估價列。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/intake-name-as-model-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(
  /\/+$/,
  "",
);
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(
  /\/+$/,
  "",
);
const SHOTS =
  process.env.SMOKE_SHOTS ??
  join(homedir(), "tmp", "lu-camp-shots", "intake-name-as-model");
const RUN = String(Date.now()).slice(-6);
const NAME = `焚火台 M${RUN}`;
const BRAND = `Snow Peak-${RUN}`;
const CATEGORY = `焚火台類${RUN}`;
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

async function api(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

async function seller(token, name) {
  return (
    await api(token, "POST", "/api/v1/contacts", {
      name,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
    })
  ).json;
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
let mgr = null;
let originalRequire = null;

try {
  mgr = (
    await api(null, "POST", "/api/v1/auth/login", {
      username: "dev-manager",
      password: "dev-test-123456",
    })
  ).json.access_token;
  originalRequire = (await api(mgr, "GET", "/api/v1/settings")).json
    .require_acquisition_affidavit;
  await api(mgr, "PATCH", "/api/v1/settings", {
    require_acquisition_affidavit: false,
  });
  await api(mgr, "POST", "/api/v1/cash-sessions/open", {
    opening_float: "1000",
  });
  await api(mgr, "POST", "/api/v1/categories", { name: CATEGORY });
  const first = await seller(mgr, `型號賣家-${RUN}`);
  const batch = (
    await api(mgr, "POST", "/api/v1/intake-batches", {
      contact_id: first.id,
      declared_item_count: 1,
    })
  ).json;
  const line = (
    await api(mgr, "POST", `/api/v1/intake-batches/${batch.id}/lines`, {
      short_name: NAME,
      qty: 1,
      acquisition_type: "BUYOUT",
      reference_price: "3000",
      discount_pct: 50,
      expected_listed_price: "1500",
      deal_cost: "700",
      grade: "B",
    })
  ).json;
  await api(mgr, "POST", `/api/v1/intake-batches/${batch.id}/ready`);
  await api(
    mgr,
    "PATCH",
    `/api/v1/intake-batches/${batch.id}/lines/${line.id}/disposition`,
    {
      disposition: "ACCEPTED",
      accepted_qty: 1,
    },
  );
  const paid = await api(
    mgr,
    "POST",
    `/api/v1/intake-batches/${batch.id}/pay`,
    { payout_method: "CASH" },
  );
  ok(
    "準備：付款完成",
    paid.json?.status === "PAID",
    JSON.stringify(paid.json?.detail ?? ""),
  );

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  const labels = [];
  await page.route("**/print/label", (route) => {
    labels.push(route.request().postDataJSON());
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: '{"status":"ok"}',
    });
  });

  // 整理上架：建品牌 → 型號旁的「品名」
  await page.goto(`${BASE}/acquisition/intake/${batch.id}/listing`, {
    waitUntil: "networkidle",
  });
  const card = page.locator(".intake-list-item").first();
  await card.waitFor();
  const nameButton = card.getByRole("button", { name: "品名", exact: true });
  ok("沒選品牌時「品名」按不了", await nameButton.isDisabled());
  await card.getByLabel("品牌", { exact: true }).fill(BRAND);
  await card.getByRole("button", { name: `＋ 建立「${BRAND}」` }).click();
  await card.getByTestId("combo-selected").filter({ hasText: BRAND }).waitFor();
  await page.screenshot({ path: join(SHOTS, "01-before-name-button.png") });
  await nameButton.click();
  await card
    .getByTestId("combo-selected")
    .filter({ hasText: NAME })
    .waitFor({ timeout: 8000 });
  ok("按「品名」後型號＝簡稱", true, NAME);
  ok(
    "品名也是型號",
    (await card.locator("summary").innerText()).includes(`品名：${NAME}`),
  );
  await page.screenshot({ path: join(SHOTS, "02-name-as-model.png") });

  await card.getByLabel("分類", { exact: true }).fill(CATEGORY);
  await card.getByRole("option", { name: CATEGORY, exact: true }).click();
  await page.getByRole("button", { name: /上架勾選的 1 件/ }).click();
  await page.getByText(/已上架 1 件，1 張標籤已送出列印/).waitFor({ timeout: 10000 });
  ok(
    "上架後標籤印品牌與型號品名",
    labels.length === 1 && labels[0].brand === BRAND && labels[0].name === NAME,
    JSON.stringify(labels),
  );

  // 另一位客人：估價打型號的一部分
  const second = await seller(mgr, `型號建議賣家-${RUN}`);
  const next = (
    await api(mgr, "POST", "/api/v1/intake-batches", {
      contact_id: second.id,
      declared_item_count: 1,
      prefill_lines: true,
    })
  ).json;
  await page.goto(`${BASE}/acquisition/intake/${next.id}`, {
    waitUntil: "networkidle",
  });
  await page.getByRole("button", { name: "1 號 詳細" }).click();
  const form = page.getByRole("form", { name: /修改第 1 列/ });
  const input = form.getByLabel("商品簡稱");
  await input.fill("");
  await input.pressSequentially(`M${RUN}`, { delay: 30 });
  const menu = form.getByRole("listbox", { name: "以前估過的商品" });
  await menu.waitFor({ timeout: 8000 });
  const text = (await menu.getByRole("option").first().textContent()) ?? "";
  ok(
    "打型號找得到、寫「上架為 品牌 型號」",
    text.includes(`上架為 ${BRAND} ${NAME}`),
    text,
  );
  await page.screenshot({ path: join(SHOTS, "03-model-suggestion.png") });
  await menu.getByRole("option").first().click();
  await form.getByRole("button", { name: "帶入" }).click();
  await form.getByRole("button", { name: "儲存詳細" }).click();
  await page.waitForFunction(
    async ([apiBase, id, auth]) => {
      const r = await fetch(`${apiBase}/api/v1/intake-batches/${id}`, {
        headers: { Authorization: `Bearer ${auth}` },
      });
      return (await r.json()).lines?.[0]?.product_model_id != null;
    },
    [API, next.id, mgr],
    { timeout: 8000 },
  );
  const saved = (await api(mgr, "GET", `/api/v1/intake-batches/${next.id}`))
    .json.lines[0];
  ok(
    "帶入後估價列有簡稱、收購價、品牌、型號",
    saved.short_name === NAME &&
      saved.deal_cost === "700" &&
      saved.brand_id != null &&
      saved.product_model_id != null,
    JSON.stringify([
      saved.short_name,
      saved.deal_cost,
      saved.brand_id,
      saved.product_model_id,
    ]),
  );
  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  ok("流程例外", false, String(error));
  await page
    .screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true })
    .catch(() => {});
} finally {
  if (mgr !== null && originalRequire !== null) {
    await api(mgr, "PATCH", "/api/v1/settings", {
      require_acquisition_affidavit: originalRequire,
    });
  }
  await browser.close();
}
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
if (failures.length > 0) process.exitCode = 1;
