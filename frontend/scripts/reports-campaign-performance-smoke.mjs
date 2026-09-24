// 活動成效報表煙霧（docs/40 P1d）：兩個只限不同品牌的活動同時進行、各賣一件（其中一筆取消
// 另一個全館活動）→ 報表「促銷 → 活動成效」各活動只算自己套到的商品，並列出可疊加、指定範圍、
// 這筆不套用次數。需 backend + frontend 已起、已 seed dev-manager。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/reports-campaign-performance-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";
import { openReport } from "./_reports.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "reports-campaign-performance");
const RUN = String(Date.now()).slice(-6);
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body, headers = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}: ${text}`);
  return text ? JSON.parse(text) : null;
}

const browser = await chromium.launch();
const campaigns = [];
let token = null;
try {
  ({ access_token: token } = await api(null, "POST", "/api/v1/auth/login", {
    username: "dev-manager",
    password: "dev-test-123456",
  }));
  const current = await api(token, "GET", "/api/v1/cash-sessions/current");
  if (current === null) await api(token, "POST", "/api/v1/cash-sessions/open", { opening_float: "3000" });
  const sp = await api(token, "POST", "/api/v1/brands", { name: `Snow Peak-${RUN}` });
  const co = await api(token, "POST", "/api/v1/brands", { name: `Coleman-${RUN}` });
  const now = Date.now();
  const make = async (name, pct, stackable, targets) => {
    const c = await api(token, "POST", "/api/v1/campaigns", {
      name,
      discount_pct: pct,
      starts_at: new Date(now - 86400000).toISOString(),
      ends_at: new Date(now + 86400000).toISOString(),
      applies_owned_serialized: true,
      applies_owned_bulk: true,
      applies_catalog: false,
      applies_consignment: false,
      stackable,
      targets,
    });
    await api(token, "POST", `/api/v1/campaigns/${c.id}/activate`);
    campaigns.push(c);
    return c;
  };
  const spCamp = await make(`SP 八折 ${RUN}`, 20, true, [
    { mode: "INCLUDE", target_type: "BRAND", target_id: sp.id },
  ]);
  const coCamp = await make(`Coleman 九折 ${RUN}`, 10, false, [
    { mode: "INCLUDE", target_type: "BRAND", target_id: co.id },
  ]);
  const extra = await make(`全館加碼九五折 ${RUN}`, 5, true, []);

  const seller = await api(token, "POST", "/api/v1/contacts", {
    name: `報表活動賣方 ${RUN}`,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER"],
  });
  const acq = await api(
    token,
    "POST",
    "/api/v1/acquisitions",
    {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [
        { name: `SP 焚火台 ${RUN}`, grade: "A", brand_id: sp.id, listed_price: "1000", acquisition_cost: "300" },
        { name: `Coleman 營燈 ${RUN}`, grade: "A", brand_id: co.id, listed_price: "1000", acquisition_cost: "300" },
      ],
    },
    { "Idempotency-Key": `rcp-${RUN}` },
  );
  const [spCode, coCode] = acq.item_codes;
  // SP：八折 × 全館九五折（兩個都可疊加）→ 1000 × 0.8 = 800，× 0.95 = 760
  const s1 = await api(token, "POST", "/api/v1/sales", { lines: [{ line_type: "SERIALIZED", item_code: spCode }] }, { "Idempotency-Key": `rcp1-${RUN}` });
  // Coleman：九折（不可疊加）vs 九五折 → 取九折 900；這筆另外把全館九五折按了不套用（本來就沒用到，但留紀錄）
  const s2 = await api(
    token,
    "POST",
    "/api/v1/sales",
    {
      lines: [{ line_type: "SERIALIZED", item_code: coCode }],
      disabled_campaigns: [{ campaign_id: extra.id, reason: "煙霧測試" }],
    },
    { "Idempotency-Key": `rcp2-${RUN}` },
  );
  ok("兩筆成交：SP 760、Coleman 900", s1.total === "760" && s2.total === "900", `${s1.total} / ${s2.total}`);

  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  page.on("pageerror", (e) => ok("頁面無 JS 例外", false, String(e)));
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
  await openReport(page, "活動成效");
  // 名稱格裡還有「可疊加」「只限：…」，用包含比對找列。
  const row = (name) => page.locator(".inv-table tbody tr", { hasText: name });
  await row(`SP 八折 ${RUN}`).waitFor({ timeout: 15000 });
  const spText = (await row(`SP 八折 ${RUN}`).innerText()).replace(/\s+/g, " ");
  const coText = (await row(`Coleman 九折 ${RUN}`).innerText()).replace(/\s+/g, " ");
  const exText = (await row(`全館加碼九五折 ${RUN}`).innerText()).replace(/\s+/g, " ");
  ok("SP 活動：可疊加、只限 SP、只算 SP 那筆（營業額 760）", spText.includes("可疊加") && spText.includes(`只限：Snow Peak-${RUN}`) && spText.includes("760"), spText);
  ok("Coleman 活動：只算 Coleman 那筆（營業額 900）", coText.includes("900") && !coText.includes("760"), coText);
  ok("全館九五折：疊在 SP 上、被取消 1 次", exText.includes("760") && / 1$/.test(exText.trim()), exText);
  await page.screenshot({ path: join(SHOTS, "01-campaign-performance.png"), fullPage: true });
} catch (error) {
  ok("流程例外", false, String(error));
  const p = browser.contexts().flatMap((c) => c.pages())[0];
  if (p) await p.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
} finally {
  await browser.close();
  for (const c of campaigns) await api(token, "POST", `/api/v1/campaigns/${c.id}/end`).catch(() => {});
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exitCode = failed > 0 ? 1 : 0;
