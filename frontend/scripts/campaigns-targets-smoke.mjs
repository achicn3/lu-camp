// 門市活動 v2 管理頁煙霧（docs/40 P1b）：在畫面上建立「只限某品牌」的活動並啟用，
// 同時另有一個全館九折；以結帳試算確認：該品牌商品取七折（最划算、不疊加），其他商品九折。
// 需 backend + frontend 已起、已 seed dev-manager。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/campaigns-targets-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "campaigns-targets");
const RUN = String(Date.now()).slice(-6);
const BRAND = `Snow Peak-${RUN}`;
const OTHER = `Coleman-${RUN}`;
const CAMPAIGN = `${BRAND} 七折`;
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(path, { method = "GET", token, body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(method === "POST" ? { "Idempotency-Key": `ct-${RUN}-${Math.random()}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  return response.json();
}

function taipeiLocal(offsetDays) {
  const d = new Date(Date.now() + offsetDays * 86_400_000 + 8 * 3_600_000);
  return d.toISOString().slice(0, 16);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current === null) {
    await apiJson("/api/v1/cash-sessions/open", { method: "POST", token, body: { opening_float: "5000" } });
  }
  const brand = await apiJson("/api/v1/brands", { method: "POST", token, body: { name: BRAND } });
  const other = await apiJson("/api/v1/brands", { method: "POST", token, body: { name: OTHER } });
  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body: { name: `活動測試賣家-${RUN}`, phone: uniquePhone(), national_id: validNationalId(), roles: ["SELLER"] },
  });
  const acq = await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [
        { name: `${BRAND} 焚火台`, grade: "A", brand_id: brand.id, acquisition_cost: "300", listed_price: "1000" },
        { name: `${OTHER} 營燈`, grade: "A", brand_id: other.id, acquisition_cost: "300", listed_price: "1000" },
      ],
    },
  });
  const [spCode, otherCode] = acq.item_codes;
  // 另一個全館九折（不可疊加）：同時進行
  const storewide = await apiJson("/api/v1/campaigns", {
    method: "POST",
    token,
    body: {
      name: `全館九折-${RUN}`,
      discount_pct: 10,
      starts_at: new Date(Date.now() - 86_400_000).toISOString(),
      ends_at: new Date(Date.now() + 86_400_000).toISOString(),
      applies_owned_serialized: true,
      applies_owned_bulk: true,
      applies_catalog: false,
      applies_consignment: false,
      stackable: false,
      targets: [],
    },
  });
  await apiJson(`/api/v1/campaigns/${storewide.id}/activate`, { method: "POST", token });
  ok("造出兩個品牌各一件商品，且已有全館九折進行中", true);

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });

  await page.getByLabel("活動名稱").fill(CAMPAIGN);
  await page.getByLabel("折扣 %（1-99）").fill("30");
  await page.getByLabel("開始時間").fill(taipeiLocal(-1));
  await page.getByLabel("結束時間").fill(taipeiLocal(1));
  const scope = page.getByRole("group", { name: "指定商品（選填）" });
  await scope.getByLabel("範圍類型").selectOption("BRAND");
  await scope.getByLabel("搜尋品牌").fill(BRAND);
  await scope.getByRole("button", { name: `加入 ${BRAND}`, exact: true }).click();
  const chips = await scope.locator(".campaign-target-chips").innerText();
  ok("選好品牌後出現在「只套用在」", chips.includes("只套用在") && chips.includes(BRAND), chips);
  await page.screenshot({ path: join(SHOTS, "01-form-with-brand.png"), fullPage: true });

  await page.getByRole("button", { name: "建立活動" }).click();
  const row = page.locator("tr", { has: page.getByText(CAMPAIGN, { exact: true }) });
  await row.waitFor();
  ok("清單顯示指定範圍", (await row.innerText()).includes(`只限：${BRAND}`));
  await row.getByRole("button", { name: "啟用" }).click();
  await row.getByText("生效中").waitFor();
  ok("可以跟全館九折同時啟用", true);
  await page.screenshot({ path: join(SHOTS, "02-list-two-active.png"), fullPage: true });

  const quote = await apiJson("/api/v1/sales/quote", {
    method: "POST",
    token,
    body: {
      lines: [
        { line_type: "SERIALIZED", item_code: spCode },
        { line_type: "SERIALIZED", item_code: otherCode },
      ],
    },
  });
  const [spLine, otherLine] = quote.lines;
  ok(
    "指定品牌的商品取七折（比九折划算、不疊加）",
    spLine.unit_price === "700" && spLine.campaigns.length === 1 && spLine.campaigns[0].name === CAMPAIGN,
    JSON.stringify(spLine.campaigns),
  );
  ok("其他品牌只有全館九折", otherLine.unit_price === "900", otherLine.unit_price);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  ok("手機寬度整頁不橫向捲動", !overflow);
  await page.screenshot({ path: join(SHOTS, "03-mobile.png"), fullPage: true });

  // 收尾：結束兩個活動，避免影響同一個資料庫的其他煙霧
  const listed = await apiJson("/api/v1/campaigns?status=ACTIVE", { token });
  for (const c of listed) {
    if (c.id === storewide.id || c.name === CAMPAIGN) {
      await apiJson(`/api/v1/campaigns/${c.id}/end`, { method: "POST", token });
    }
  }

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  ok("流程例外", false, String(error));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exitCode = failed > 0 ? 1 : 0;
