// 雙瀏覽器時區驗收：同一筆 DB 瞬間、今日查詢邊界、datetime-local payload 必須一致。
// 需以隔離測試 DB 啟動 backend/frontend 後執行（docs/20）。
import { mkdirSync } from "node:fs";

import { chromium } from "playwright";

import { taipeiDateForScript } from "./_taipei-date.mjs";

const BASE = (process.env.BASE_URL ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-shots/timezone";
const USER = process.env.SEED_USER ?? "dev-manager";
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";
mkdirSync(SHOTS, { recursive: true });

async function api(path, { method = "GET", token, body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${text}`);
  return text ? JSON.parse(text) : null;
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"], input#username', USER);
  await page.fill('input[name="password"], input#password', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 15_000 });
}

async function inspect(browser, timezoneId, campaignName, cleanupIds) {
  const context = await browser.newContext({
    timezoneId,
    viewport: { width: 1280, height: 1000 },
  });
  try {
    const page = await context.newPage();
    await login(page);

    await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
    const reportDate = await page.locator('input[type="date"]').first().inputValue();

    const salesRequest = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return (
        request.method() === "GET" &&
        url.pathname === "/api/v1/sales" &&
        url.searchParams.has("from")
      );
    });
    await page.goto(`${BASE}/sales`, { waitUntil: "domcontentloaded" });
    const salesFrom = new URL((await salesRequest).url()).searchParams.get("from");

    await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });
    const knownRow = page.locator("tbody tr", { hasText: campaignName });
    await knownRow.waitFor();
    const knownStart = (await knownRow.locator("td").nth(2).innerText()).trim();

    await page.getByLabel("活動名稱").fill(`TZ-${timezoneId}-${Date.now()}`);
    await page.getByLabel("折扣 %（1-99）").fill("10");
    const dateInputs = page.locator('input[type="datetime-local"]');
    await dateInputs.nth(0).fill("2027-01-02T03:04");
    await dateInputs.nth(1).fill("2027-01-03T04:05");
    const createResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === "POST" && url.pathname === "/api/v1/campaigns";
    });
    await page.getByRole("button", { name: "建立活動" }).click();
    const response = await createResponse;
    if (!response.ok()) throw new Error(`${timezoneId} 建立活動失敗：${response.status()}`);
    const requestBody = response.request().postDataJSON();
    const created = await response.json();
    cleanupIds.push(created.id);

    await page.screenshot({
      path: `${SHOTS}/${timezoneId.replaceAll("/", "-")}.png`,
      fullPage: true,
    });
    return { reportDate, salesFrom, knownStart, requestBody };
  } finally {
    await context.close();
  }
}

const loginResult = await api("/api/v1/auth/login", {
  method: "POST",
  body: { username: USER, password: PASS },
});
const token = loginResult.access_token;
const run = Date.now();
const campaignName = `時區一致性-${run}`;
const cleanupIds = [];
let browser;
try {
  const known = await api("/api/v1/campaigns", {
    method: "POST",
    token,
    body: {
      name: campaignName,
      discount_pct: 10,
      starts_at: "2026-07-21T16:30:00Z",
      ends_at: "2026-07-22T16:30:00Z",
      applies_owned_serialized: true,
      applies_owned_bulk: true,
      applies_catalog: true,
      applies_consignment: false,
    },
  });
  cleanupIds.push(known.id);

  browser = await chromium.launch();
  const utc = await inspect(browser, "UTC", campaignName, cleanupIds);
  const taipei = await inspect(browser, "Asia/Taipei", campaignName, cleanupIds);

  const expectedDate = taipeiDateForScript();
  const expectedFrom = new Date(`${expectedDate}T00:00:00+08:00`).toISOString();
  const expectedPayload = {
    starts_at: "2027-01-01T19:04:00.000Z",
    ends_at: "2027-01-02T20:05:00.000Z",
  };
  for (const [label, result] of Object.entries({ UTC: utc, Taipei: taipei })) {
    if (result.reportDate !== expectedDate) throw new Error(`${label} 報表日期 ${result.reportDate}`);
    if (result.salesFrom !== expectedFrom) throw new Error(`${label} 銷售起界 ${result.salesFrom}`);
    if (result.knownStart !== "2026/07/22 00:30") throw new Error(`${label} 顯示 ${result.knownStart}`);
    if (
      result.requestBody.starts_at !== expectedPayload.starts_at ||
      result.requestBody.ends_at !== expectedPayload.ends_at
    ) {
      throw new Error(`${label} 活動 payload ${JSON.stringify(result.requestBody)}`);
    }
  }
  console.log("TIMEZONE SMOKE PASS：UTC / Asia/Taipei 瀏覽器的日期、顯示與 API 邊界一致");
} finally {
  try {
    if (browser) await browser.close();
  } finally {
    for (const id of cleanupIds) {
      try {
        await api(`/api/v1/campaigns/${id}/cancel`, { method: "POST", token });
      } catch {
        // 隔離測試 DB 會整庫清除；此處僅盡力保持互動式執行乾淨。
      }
    }
  }
}
