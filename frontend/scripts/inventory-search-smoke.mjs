// 庫存搜尋框：條碼、種類、品牌都搜得到（店主 2026-10-01）的瀏覽器煙霧。
//
// 造一件「品名裡沒有品牌字樣」的序號品＋一堆有品牌的散裝，從畫面分別用品牌、型號、種類、
// 條碼搜，確認找得到那一件、找不到別的。需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/inventory-search-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-inventory-search-shots");
const RUN = String(Date.now()).slice(-6);
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
      ...(method === "POST" ? { "Idempotency-Key": `invsearch-${RUN}-${Math.random()}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  }
  return response.status === 204 ? null : response.json();
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
await skipOpeningCheckRedirect(page);

async function search(q) {
  await page.getByLabel("搜尋", { exact: true }).fill(q);
  await page.getByRole("button", { name: "查詢" }).click();
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(300);
  return page.locator("table tbody tr").allInnerTexts();
}

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  if ((await apiJson("/api/v1/cash-sessions/current", { token })) === null) {
    await apiJson("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
    });
  }
  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body: {
      name: `搜尋賣家-${RUN}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
    },
  });
  const brandName = `雪峰測${RUN}`;
  const modelName = `圓頂型${RUN}`;
  const categoryName = `帳篷類${RUN}`;
  const brand = await apiJson("/api/v1/brands", { method: "POST", token, body: { name: brandName } });
  const model = await apiJson("/api/v1/product-models", {
    method: "POST",
    token,
    body: { brand_id: brand.id, name: modelName },
  });
  const category = await apiJson("/api/v1/categories", {
    method: "POST",
    token,
    body: { name: categoryName },
  });
  const itemName = `四人帳-${RUN}`;
  const acq = await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [
        {
          name: itemName,
          brand_id: brand.id,
          product_model_id: model.id,
          category_id: category.id,
          grade: "B",
          acquisition_cost: "800",
          listed_price: "2000",
        },
      ],
    },
  });
  const code = acq.item_codes[0];
  ok("造出一件品名不含品牌的序號品", typeof code === "string", code);

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "序號品" }).click();

  const hint = await page.getByLabel("搜尋", { exact: true }).getAttribute("placeholder");
  ok("搜尋框提示寫著條碼、種類、品牌", ["條碼", "種類", "品牌"].every((w) => hint?.includes(w)), hint ?? "");

  for (const [label, q] of [
    ["品牌", brandName],
    ["型號", modelName],
    ["種類", categoryName],
    ["條碼", code],
  ]) {
    const rows = await search(q);
    ok(
      `用${label}搜得到、只有這一件`,
      rows.length === 1 && rows[0].includes(code),
      `${q} → ${rows.length} 列`,
    );
    if (label === "品牌") await page.screenshot({ path: `${SHOTS}/01-search-by-brand.png` });
  }
  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (err) {
  await page.screenshot({ path: `${SHOTS}/error.png` }).catch(() => {});
  ok("煙霧流程", false, String(err));
}

await browser.close();
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
process.exit(failures.length === 0 ? 0 : 1);
