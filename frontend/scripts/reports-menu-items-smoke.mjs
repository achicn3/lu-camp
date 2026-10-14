// 餐飲品項報表（店主 2026-10-15）瀏覽器煙霧：
// 用 API 建三個餐飲品項並結帳（拿鐵 3 杯、司康 1 個；冰美式不賣）→
// 每天看「今日餐飲」：排名表拿鐵第一、沒賣出列出冰美式 → 點「營收」改排序 →
// 賣得如何「餐飲品項」：同一張表、總覽有「比上一期」。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/reports-menu-items-smoke.mjs
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";
import { openReport } from "./_reports.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS =
  process.env.SMOKE_SHOTS ??
  join(homedir(), "tmp", "lu-camp-shots", "reports-menu-items");
const RUN = String(Date.now()).slice(-6);
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

async function call(token, method, path, body, headers = {}) {
  const resp = await fetch(`${API}/api/v1${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  if (!resp.ok) throw new Error(`${method} ${path} → ${resp.status} ${text}`);
  return text ? JSON.parse(text) : null;
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const login = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: "dev-manager",
      password: "dev-test-123456",
    }),
  });
  const token = (await login.json()).access_token;
  const current = await fetch(`${API}/api/v1/cash-sessions/current`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (current.status !== 200 || !(await current.json())) {
    await call(token, "POST", "/cash-sessions/open", { opening_float: "5000" });
  }
  const latte = await call(token, "POST", "/menu-items", {
    name: `拿鐵-${RUN}`,
    unit_price: "150",
    unit_cost: "45",
  });
  const scone = await call(token, "POST", "/menu-items", {
    name: `司康-${RUN}`,
    unit_price: "90",
  });
  await call(token, "POST", "/menu-items", {
    name: `冰美式-${RUN}`,
    unit_price: "120",
  });
  for (const lines of [
    [{ line_type: "MENU", menu_item_id: latte.id, qty: 2 }],
    [
      { line_type: "MENU", menu_item_id: latte.id, qty: 1 },
      { line_type: "MENU", menu_item_id: scone.id, qty: 1 },
    ],
  ]) {
    await call(
      token,
      "POST",
      "/sales",
      { lines, service_mode: "TAKEOUT" },
      { "Idempotency-Key": randomUUID() },
    );
  }

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });

  await openReport(page, "今日餐飲");
  const table = page.getByRole("table", { name: "餐飲品項排名" });
  await table.waitFor({ timeout: 10000 });
  const rowOf = (name) =>
    table.locator("tbody tr", { has: page.locator(`td:text-is("${name}")`) });
  const latteRow = (await rowOf(`拿鐵-${RUN}`).innerText()).replace(
    /\s+/g,
    " ",
  );
  ok(
    "今日餐飲：拿鐵 3 份、營收 450、毛利 315",
    latteRow.includes(" 3 ") &&
      latteRow.includes("450") &&
      latteRow.includes("315"),
    latteRow,
  );
  const unsold = (await page.locator(".rpt-unsold").innerText()).replace(
    /\s+/g,
    " ",
  );
  ok("沒賣出的品項列出冰美式", unsold.includes(`冰美式-${RUN}`), unsold);
  ok("總覽寫「比前一天」", (await page.getByText("比前一天").count()) > 0);
  await page.screenshot({ path: join(SHOTS, "01-today.png"), fullPage: true });

  await table.getByRole("button", { name: /營收/ }).click();
  ok(
    "點「營收」後依營收排序",
    (await table.locator("th[aria-sort=descending]").innerText()).includes(
      "營收",
    ),
  );

  await openReport(page, "餐飲品項");
  await page
    .getByRole("table", { name: "餐飲品項排名" })
    .waitFor({ timeout: 10000 });
  ok(
    "餐飲品項：總覽寫「比上一期」",
    (await page.getByText("比上一期").count()) > 0,
  );
  ok(
    "餐飲品項：有分類小計表",
    (await page.getByRole("table", { name: "餐飲分類小計" }).count()) === 1,
  );
  await page.screenshot({ path: join(SHOTS, "02-range.png"), fullPage: true });
  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  ok("流程例外", false, String(error));
  await page
    .screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true })
    .catch(() => {});
} finally {
  await browser.close();
}
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
if (failures.length > 0) process.exitCode = 1;
