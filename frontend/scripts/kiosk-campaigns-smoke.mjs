// 客顯顯示「每件是哪個活動折的」煙霧（docs/40 §7、P1c）：配對客顯 → 兩個可疊加九折同時進行 →
// 店員購物車（經 API）放一件商品 → 客顯該件列出兩個活動名稱；取消其中一個後只剩一個。
// 需 backend + frontend 已起、已 seed dev-manager 與 dev-kiosk（SEED_USER_ROLE=KIOSK）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/kiosk-campaigns-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "kiosk-campaigns");
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
let terminalId = null;
let cartRevision = null;
try {
  ({ access_token: token } = await api(null, "POST", "/api/v1/auth/login", {
    username: "dev-manager",
    password: "dev-test-123456",
  }));
  const page = await browser.newPage({ viewport: { width: 834, height: 1112 } });
  await page.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-kiosk");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("啟用裝置")');
  await page.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  const pairingCode = (await page.textContent(".kiosk-pairing-code"))?.trim();
  const terminal = await api(token, "POST", "/api/v1/customer-display/terminals", {
    installation_id: crypto.randomUUID(),
    name: `活動客顯櫃檯 ${RUN}`,
  });
  terminalId = terminal.id;
  await api(token, "POST", `/api/v1/customer-display/terminals/${terminalId}/pair`, {
    pairing_code: pairingCode,
  });
  ok("客顯配對", true);

  const now = Date.now();
  for (const name of [`露營季九折 ${RUN}`, `會員九折 ${RUN}`]) {
    const c = await api(token, "POST", "/api/v1/campaigns", {
      name,
      discount_pct: 10,
      starts_at: new Date(now - 86400000).toISOString(),
      ends_at: new Date(now + 86400000).toISOString(),
      applies_owned_serialized: true,
      applies_owned_bulk: true,
      applies_catalog: false,
      applies_consignment: false,
      stackable: true,
      targets: [],
    });
    await api(token, "POST", `/api/v1/campaigns/${c.id}/activate`);
    campaigns.push(c);
  }
  const current = await api(token, "GET", "/api/v1/cash-sessions/current");
  if (current === null) {
    await api(token, "POST", "/api/v1/cash-sessions/open", { opening_float: "2000" });
  }
  const seller = await api(token, "POST", "/api/v1/contacts", {
    name: `客顯活動賣方 ${RUN}`,
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
      items: [{ name: `客顯帳篷 ${RUN}`, grade: "A", listed_price: "1000", acquisition_cost: "300" }],
    },
    { "Idempotency-Key": `kc-${RUN}` },
  );
  const code = acq.item_codes[0];

  const put = (body) =>
    api(token, "PUT", `/api/v1/customer-display/terminals/${terminalId}/cart`, body);
  let cart = await put({
    expected_revision: null,
    lines: [{ line_type: "SERIALIZED", item_code: code }],
  });
  cartRevision = cart.revision;
  const item = page.locator(".kiosk-cart-item", { hasText: `客顯帳篷 ${RUN}` });
  await item.waitFor({ timeout: 15000 });
  await page.waitForFunction(
    (run) => document.body.innerText.includes(`會員九折 ${run}`),
    RUN,
    { timeout: 15000 },
  );
  const text = await item.innerText();
  ok(
    "客顯列出這件套到的兩個活動",
    text.includes(`露營季九折 ${RUN}`) && text.includes(`會員九折 ${RUN}`),
    text.replace(/\s+/g, " "),
  );
  await page.screenshot({ path: join(SHOTS, "01-two-campaigns.png"), fullPage: true });

  cart = await put({
    expected_revision: cartRevision,
    lines: [{ line_type: "SERIALIZED", item_code: code }],
    disabled_campaigns: [{ campaign_id: campaigns[1].id, reason: null }],
  });
  cartRevision = cart.revision;
  await page.waitForFunction(
    (run) => !document.body.innerText.includes(`會員九折 ${run}`),
    RUN,
    { timeout: 15000 },
  );
  ok("店員取消會員九折後，客顯只剩一個活動", (await item.innerText()).includes(`露營季九折 ${RUN}`));
  await page.screenshot({ path: join(SHOTS, "02-one-campaign.png"), fullPage: true });
} catch (error) {
  ok("流程例外", false, String(error));
  const p = browser.contexts().flatMap((c) => c.pages())[0];
  if (p) await p.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
} finally {
  await browser.close();
  if (token && terminalId !== null && cartRevision !== null) {
    await api(token, "POST", `/api/v1/customer-display/terminals/${terminalId}/cart/cancel`, {
      expected_revision: cartRevision,
      reason: "煙霧測試結束",
    }).catch(() => {});
  }
  for (const c of campaigns) {
    await api(token, "POST", `/api/v1/campaigns/${c.id}/end`).catch(() => {});
  }
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exitCode = failed > 0 ? 1 : 0;
