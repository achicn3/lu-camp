// 門市活動 v2 P4 煙霧（docs/40）：在畫面上建立「帳篷＋椅子 組合價 7000、可疊加」（兩格各指定一個品牌）
// 並啟用，另有只限這兩個品牌的可疊加九折 → POS 掃帳篷 6000、椅子 2000 → 組合 7000 再九折＝應付 6,300、
// 兩行都標「組合價・第 1 組」→ 結帳完成 → 只退帳篷被擋（必須整組退）→ 整組退成功、退 6,300。
// 需 backend + frontend 已起、已 seed dev-manager。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/campaigns-bundle-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "campaigns-bundle");
const RUN = String(Date.now()).slice(-6);
const TENT_BRAND = `帳篷牌-${RUN}`;
const CHAIR_BRAND = `椅子牌-${RUN}`;
const CAMPAIGN = `帳篷椅子組-${RUN}`;
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
      ...(method === "POST" ? { "Idempotency-Key": `bd-${RUN}-${Math.random()}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  return response.json();
}

async function apiStatus(path, { token, body }) {
  const response = await fetch(`${API}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "Idempotency-Key": `bd-${RUN}-${Math.random()}`,
    },
    body: JSON.stringify(body),
  });
  return { status: response.status, text: await response.text() };
}

function taipeiLocal(offsetDays) {
  const d = new Date(Date.now() + offsetDays * 86_400_000 + 8 * 3_600_000);
  return d.toISOString().slice(0, 16);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1300 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
let token = "";
let extraId = null;

async function addBrand(slotName, brandName) {
  const slot = page.getByRole("group", { name: slotName });
  await slot.getByLabel("搜尋品牌").fill(brandName);
  await slot.getByRole("button", { name: `加入 ${brandName}`, exact: true }).click();
}

try {
  ({ access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  }));
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current === null) {
    await apiJson("/api/v1/cash-sessions/open", { method: "POST", token, body: { opening_float: "5000" } });
  }
  const tentBrand = await apiJson("/api/v1/brands", { method: "POST", token, body: { name: TENT_BRAND } });
  const chairBrand = await apiJson("/api/v1/brands", { method: "POST", token, body: { name: CHAIR_BRAND } });
  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body: { name: `組合測試賣家-${RUN}`, phone: uniquePhone(), national_id: validNationalId(), roles: ["SELLER"] },
  });
  const names = [`帳篷 ${RUN}`, `椅子 ${RUN}`];
  const acq = await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [
        { name: names[0], grade: "A", brand_id: tentBrand.id, acquisition_cost: "2000", listed_price: "6000" },
        { name: names[1], grade: "A", brand_id: chairBrand.id, acquisition_cost: "500", listed_price: "2000" },
      ],
    },
  });
  ok("造出帳篷 6000、椅子 2000", acq.item_codes.length === 2);
  const extra = await apiJson("/api/v1/campaigns", {
    method: "POST",
    token,
    body: {
      name: `兩牌九折-${RUN}`,
      discount_pct: 10,
      starts_at: new Date(Date.now() - 86_400_000).toISOString(),
      ends_at: new Date(Date.now() + 86_400_000).toISOString(),
      applies_owned_serialized: true,
      applies_owned_bulk: true,
      applies_catalog: false,
      applies_consignment: false,
      stackable: true,
      bundle_slots: [],
      targets: [
        { mode: "INCLUDE", target_type: "BRAND", target_id: tentBrand.id },
        { mode: "INCLUDE", target_type: "BRAND", target_id: chairBrand.id },
      ],
    },
  });
  extraId = extra.id;
  await apiJson(`/api/v1/campaigns/${extra.id}/activate`, { method: "POST", token });

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });

  await page.getByLabel("活動名稱").fill(CAMPAIGN);
  await page.getByLabel("組合價", { exact: true }).check();
  await page.getByLabel("組合價（含稅，元）").fill("7000");
  await addBrand("第 1 樣商品", TENT_BRAND);
  await addBrand("第 2 樣商品", CHAIR_BRAND);
  await page.getByLabel("開始時間").fill(taipeiLocal(-1));
  await page.getByLabel("結束時間").fill(taipeiLocal(1));
  await page.getByLabel("可以和其他活動疊加").check();
  await page.screenshot({ path: join(SHOTS, "01-form.png"), fullPage: true });
  await page.getByRole("button", { name: "建立活動" }).click();
  const row = page.locator("tr", { has: page.getByText(CAMPAIGN, { exact: true }) });
  await row.waitFor();
  const rowText = await row.innerText();
  ok(
    "清單顯示組合價與組合內容",
    rowText.includes("組合價 $7,000") && rowText.includes(`組合：${TENT_BRAND} ×1 ＋ ${CHAIR_BRAND} ×1`),
    rowText.replace(/\s+/g, " "),
  );
  await row.getByRole("button", { name: "啟用" }).click();
  await row.getByText("生效中").waitFor();
  await page.screenshot({ path: join(SHOTS, "02-list.png"), fullPage: true });

  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector("text=這筆不開發票");
  for (const [i, code] of acq.item_codes.entries()) {
    await page.fill('input[name="code"]', code);
    await page.press('input[name="code"]', "Enter");
    await page.locator(".pos-cart tbody tr", { hasText: names[i] }).waitFor();
  }
  await page.waitForFunction(() => document.querySelector(".pos-total strong")?.textContent?.includes("6,300"));
  ok("湊齊自動套用、再疊九折：應付 6,300", true);
  const rows = await page.locator(".pos-cart tbody tr").allInnerTexts();
  ok("兩行都標「組合價・第 1 組」", rows.every((t) => t.includes("組合價・第 1 組")), rows.join(" | ").replace(/\s+/g, " "));
  ok("組合價的行不出現「改送這件」", rows.every((t) => !t.includes("改送這件")));
  await page.screenshot({ path: join(SHOTS, "03-pos-cart.png"), fullPage: true });

  await page.waitForFunction(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent?.trim() === "結帳");
    return b && !b.disabled;
  });
  await page.getByRole("button", { name: "結帳" }).click();
  await page.waitForSelector("text=已完成");
  const completeText = await page.locator(".pos-complete").innerText();
  ok("結帳完成（6,300）", /6,?300/.test(completeText), completeText.replace(/\s+/g, " ").slice(0, 80));
  await page.screenshot({ path: join(SHOTS, "04-complete.png"), fullPage: true });

  const saleId = Number(/#(\d+)/.exec(completeText)?.[1]);
  const sale = await apiJson(`/api/v1/sales/${saleId}`, { token });
  const tentLine = sale.lines.find((l) => l.description === names[0]);
  const chairLine = sale.lines.find((l) => l.description === names[1]);
  const partial = await apiStatus("/api/v1/returns", {
    token,
    body: { sale_id: saleId, reason: "煙霧測試", lines: [{ sale_line_id: tentLine.id, qty: 1 }] },
  });
  ok("只退帳篷被擋（必須整組退）", partial.status === 422 && partial.text.includes("整組"), partial.text.slice(0, 120));
  const whole = await apiStatus("/api/v1/returns", {
    token,
    body: {
      sale_id: saleId,
      reason: "煙霧測試",
      lines: [
        { sale_line_id: tentLine.id, qty: 1 },
        { sale_line_id: chairLine.id, qty: 1 },
      ],
    },
  });
  ok("整組退成功、退 6,300", whole.status === 201 && JSON.parse(whole.text).refund_amount === "6300", whole.text.slice(0, 160));

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });
  await page.getByLabel("組合價", { exact: true }).check();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  ok("手機寬度整頁不橫向捲動", !overflow);
  await page.screenshot({ path: join(SHOTS, "05-mobile-form.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  ok("流程例外", false, String(error));
} finally {
  await browser.close();
  // 一定結束自己開的活動：留著會改變同一個資料庫裡其他煙霧的價格。
  if (token) {
    const listed = await apiJson("/api/v1/campaigns?status=ACTIVE", { token }).catch(() => []);
    for (const c of listed) {
      if (c.name === CAMPAIGN || c.id === extraId) {
        await apiJson(`/api/v1/campaigns/${c.id}/end`, { method: "POST", token }).catch(() => {});
      }
    }
  }
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exitCode = failed > 0 ? 1 : 0;
