// 收購列精簡煙霧（店主 2026-09-26）：成色一排按鈕、收購價與件數同一行、折數說明收進 ⓘ、
// 「複製這列」（品牌型號分類一起複製、下拉顯示得出名稱）、選型號自動帶上次的分類（可改）。
// 需 backend + frontend 已起、已 seed（dev-manager）。執行：node scripts/acquisition-row-streamline-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { pickGrade } from "./_acquisition.mjs";
import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "acquisition-row-streamline");
const RUN = String(Date.now()).slice(-6);
const BRAND = `Snow${RUN}`;
const MODEL = `焚火台M${RUN}`;
const CATEGORY = `焚火台${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body, headers = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const mgr = (await api(null, "POST", "/api/v1/auth/login", { username: "dev-manager", password: "dev-test-123456" })).json.access_token;
  await api(mgr, "POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  // 準備：這個型號以前收過一件，分類是「焚火台」
  const brand = (await api(mgr, "POST", "/api/v1/brands", { name: BRAND })).json;
  const model = (await api(mgr, "POST", "/api/v1/product-models", { brand_id: brand.id, name: MODEL })).json;
  const category = (await api(mgr, "POST", "/api/v1/categories", { name: CATEGORY })).json;
  const seller = (await api(mgr, "POST", "/api/v1/contacts", {
    name: `以前的賣家-${RUN}`,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER"],
  })).json;
  const past = await api(
    mgr,
    "POST",
    "/api/v1/acquisitions",
    {
      type: "BUYOUT",
      contact_id: seller.id,
      items: [{ name: MODEL, grade: "B", listed_price: "2000", acquisition_cost: "800", brand_id: brand.id, product_model_id: model.id, category_id: category.id }],
      payout_method: "CASH",
    },
    { "Idempotency-Key": `streamline-${RUN}` },
  );
  ok("準備：以前收過這個型號（分類焚火台）", past.status === 201, String(past.status));

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.waitForSelector('[role="tab"]:has-text("買斷")');
  const row = page.locator(".acq-row").first();

  // 成色按鈕、收購價與件數同一行、折數說明收進 ⓘ
  ok("成色是一排按鈕（6 個）", (await row.locator('[role="radiogroup"][aria-label="成色"] button').count()) === 6);
  await pickGrade(row, "A");
  ok("按了就選好、下方顯示完整名稱", (await row.locator('[aria-label="A 近全新/精品"]').getAttribute("aria-checked")) === "true" && (await row.innerText()).includes("A 近全新/精品"));
  const costBox = await row.getByRole("textbox", { name: "收購價", exact: true }).boundingBox();
  const qtyBox = await row.getByRole("textbox", { name: "件數", exact: true }).boundingBox();
  ok("收購價與件數在同一行", costBox !== null && qtyBox !== null && Math.abs(costBox.y - qtyBox.y) < 4);
  ok("折數說明段落收進 ⓘ（列上不再有那段長字）", !(await row.innerText()).includes("也可不填參考價與折數"));
  await page.screenshot({ path: join(SHOTS, "01-grade-buttons.png") });

  // 選型號 → 自動帶上次的分類
  const brandBox = row.getByLabel("品牌", { exact: true });
  await brandBox.click();
  await brandBox.fill(BRAND);
  await row.getByRole("option", { name: BRAND, exact: true }).click();
  const modelBox = row.getByLabel("型號", { exact: true });
  await modelBox.click();
  await modelBox.fill(MODEL);
  await row.getByRole("option", { name: MODEL, exact: true }).click();
  await row.getByText("分類照這個型號上次收的帶入，不對可以改。").waitFor({ timeout: 5000 });
  const categoryChip = row.getByTestId("combo-selected").filter({ hasText: CATEGORY });
  ok("選型號後分類自動帶入上次的「焚火台」、附提示", (await categoryChip.count()) === 1);
  await page.fill('input[aria-label="收購價"]', "900");
  await page.fill('input[aria-label="上架售價（含稅與手續費）"]', "2200");
  await page.screenshot({ path: join(SHOTS, "02-category-from-model.png") });

  // 複製這列
  await row.getByRole("button", { name: "複製這列" }).click();
  const rows = page.locator(".acq-row");
  await rows.nth(1).waitFor();
  const copy = rows.nth(1);
  ok(
    "複製出第 2 列：品牌、型號、分類都帶過去且看得到名稱",
    (await copy.getByTestId("combo-selected").filter({ hasText: BRAND }).count()) === 1 &&
      (await copy.getByTestId("combo-selected").filter({ hasText: MODEL }).count()) === 1 &&
      (await copy.getByTestId("combo-selected").filter({ hasText: CATEGORY }).count()) === 1,
  );
  ok("複製的列成色與價格也帶過去", (await copy.locator('[aria-label="A 近全新/精品"]').getAttribute("aria-checked")) === "true" && (await copy.getByRole("textbox", { name: "收購價", exact: true }).inputValue()) === "900");
  ok("原本那列收合成一行摘要", (await page.locator(".acq-row-collapsed").count()) === 1);
  await pickGrade(copy, "B");
  await copy.getByRole("textbox", { name: "上架售價（含稅與手續費）", exact: true }).fill("1900");
  await page.screenshot({ path: join(SHOTS, "03-duplicated-row.png"), fullPage: true });

  // 送出：兩件都建成
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', `精簡賣家-${RUN}`);
  await page.fill('input[aria-label="手機"]', uniquePhone());
  await page.fill('input[aria-label="身分證字號"]', validNationalId());
  await page.click('button:has-text("建立並選取")');
  await page.waitForTimeout(500);
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 10000 });
  const done = await page.locator(".acq-result").innerText();
  ok("送出成功、兩件都有條碼", (done.match(/S1-/g) ?? []).length === 2, done.split("\n").slice(0, 2).join(" "));
  const items = await api(mgr, "GET", `/api/v1/serialized-items?product_model_id=${model.id}&limit=10`);
  // 這兩件的上架售價 2200／1900（以前那件是 2000）
  const fresh = items.json.filter((i) => ["2200", "1900"].includes(i.listed_price));
  ok(
    "兩件都存了同一個分類，成色 A、B 各一",
    fresh.length === 2 && fresh.every((i) => i.category_id === category.id) && fresh.map((i) => i.grade).sort().join() === "A,B",
    JSON.stringify(fresh.map((i) => [i.grade, i.category_id])),
  );

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  console.log(String(error));
  process.exitCode = 1;
} finally {
  await browser.close();
}
