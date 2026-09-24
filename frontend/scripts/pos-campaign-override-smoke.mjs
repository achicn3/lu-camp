// 「這筆不套用」瀏覽器煙霧（docs/40 P1c）：兩個可疊加的九折同時進行（1000 → 810）→ POS 掃碼 →
// 「本筆套用的活動」列出兩個 → 對其中一個按「這筆不套用」填原因 → 應付改為 900、顯示「恢復套用」→
// 現金結帳 → 後端成交 900。
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";

import { chromium } from "playwright";
import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-shots/pos-campaign-override";
const USER = process.env.SEED_USER ?? "dev-manager";
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });
const results = [];
const ok = (name, pass, detail = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
};

async function apiJson(path, { method = "GET", token, body, headers = {}, expected = [200, 201] } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!expected.includes(res.status)) {
    throw new Error(`${method} ${path} → ${res.status}: ${text}`);
  }
  return data;
}

let browser;
let token = null;
const created = [];
try {
  ({ access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: USER, password: PASS },
  }));
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current === null) {
    await apiJson("/api/v1/cash-sessions/open", { method: "POST", token, body: { opening_float: "2000" } });
  }
  const runId = `${Date.now()}-${randomUUID().slice(0, 6)}`;
  const now = new Date();
  for (const name of [`全館九折 ${runId}`, `會員九折 ${runId}`]) {
    const camp = await apiJson("/api/v1/campaigns", {
      method: "POST",
      token,
      body: {
        name,
        discount_pct: 10,
        starts_at: new Date(now.getTime() - 86400000).toISOString(),
        ends_at: new Date(now.getTime() + 86400000).toISOString(),
        applies_owned_serialized: true,
        applies_owned_bulk: true,
        applies_catalog: false,
        applies_consignment: false,
        stackable: true,
        targets: [],
      },
    });
    await apiJson(`/api/v1/campaigns/${camp.id}/activate`, { method: "POST", token });
    created.push(camp);
  }
  ok("兩個可疊加的九折同時進行", true);

  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    expected: [201],
    body: { name: `不套用煙測賣方 ${runId}`, phone: uniquePhone(), national_id: validNationalId(), roles: ["SELLER"] },
  });
  const acq = await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": `ovr-${runId}` },
    expected: [201],
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [{ name: `不套用帳篷 ${runId}`, grade: "A", listed_price: "1000", acquisition_cost: "400" }],
    },
  });
  const code = acq.item_codes[0];

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', USER);
  await page.fill('input[name="password"]', PASS);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector("text=這筆不開發票");

  await page.fill('input[name="code"]', code);
  await page.waitForSelector(`text=不套用帳篷 ${runId}`);
  const total = () => page.locator(".pos-total strong").textContent();
  await page.waitForFunction(() => document.querySelector(".pos-total strong")?.textContent?.includes("810"));
  ok("兩個九折疊加：應付 810", (await total())?.includes("810") ?? false);
  const rowText = await page.locator(".pos-cart tbody tr").first().innerText();
  ok("購物車該行列出套到的活動", rowText.includes(`全館九折 ${runId}`) && rowText.includes(`會員九折 ${runId}`), rowText.replace(/\s+/g, " "));
  await page.screenshot({ path: `${SHOTS}/01-two-campaigns.png`, fullPage: true });

  const panel = () => page.getByRole("region", { name: "本筆套用的活動" });
  const memberRow = panel().locator("li", { hasText: `會員九折 ${runId}` });
  await memberRow.getByRole("button", { name: "這筆不套用" }).click();
  await panel().getByLabel("不套用原因").fill("客人不要會員折扣");
  await page.screenshot({ path: `${SHOTS}/02-disable-reason.png`, fullPage: true });
  await panel().getByRole("button", { name: "確定不套用" }).click();
  await page.waitForFunction(() => {
    const t = document.querySelector(".pos-total strong")?.textContent ?? "";
    return t.includes("900") && !t.includes("810");
  });
  ok("取消會員九折後應付 900", (await total())?.includes("900") ?? false);
  ok("顯示「恢復套用」", await panel().getByRole("button", { name: "恢復套用" }).isVisible());
  await page.screenshot({ path: `${SHOTS}/03-after-disable.png`, fullPage: true });

  const checkout = page.getByRole("button", { name: "結帳" });
  await page.waitForFunction(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent?.trim() === "結帳");
    return b && !b.disabled;
  });
  await checkout.click();
  await page.waitForSelector("text=已完成");
  const completeText = await page.locator(".pos-complete").innerText();
  ok("結帳完成（900）", /900/.test(completeText), completeText.replace(/\s+/g, " ").slice(0, 120));
  await page.screenshot({ path: `${SHOTS}/04-complete.png`, fullPage: true });

} catch (e) {
  ok("流程中斷", false, String(e));
  if (browser) {
    const p = browser.contexts().flatMap((c) => c.pages())[0];
    if (p) await p.screenshot({ path: `${SHOTS}/99-failure.png`, fullPage: true });
  }
} finally {
  if (browser) await browser.close();
  // 一定結束自己開的活動：留著會改變同一個資料庫裡其他煙霧的價格。
  for (const camp of created) {
    await apiJson(`/api/v1/campaigns/${camp.id}/end`, { method: "POST", token }).catch(() => {});
  }
}
const failed = results.filter((r) => !r.pass);
console.log(`\n結果：${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length ? 1 : 0);
