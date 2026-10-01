// 關帳提醒剩餘份數瀏覽器煙霧（docs/49 F4）：
// 每日限量戚風剩 3 份、司康剩 1 份 → 現金對帳頁結帳區列出兩項、預設全勾報廢 → 取消司康 →
// 結帳 → 戚風記成報廢（後端份數 0、損耗報表有 3 份）、司康不動（仍剩 1）。
//
// 會關掉目前的班別，結束時再開一個新班別，讓之後的煙霧照常可收現。
// 需 backend + frontend 已起，且指向隔離測試庫（SMOKE_ALLOW_WRITE=1）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會關班別、記報廢，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

let token = "";
async function api(method, path, body) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
}

const run = randomUUID().slice(0, 6);
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));

  const cash = await api("GET", "/api/v1/cash-sessions/current");
  if (cash.status !== 200 || cash.body === null) {
    await api("POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  }
  const made = {};
  for (const [name, qty, cost] of [
    [`戚風-${run}`, 3, "40"],
    [`司康-${run}`, 1, "30"],
  ]) {
    const item = await api("POST", "/api/v1/menu-items", {
      name,
      unit_price: "90",
      unit_cost: cost,
    });
    await api("PATCH", `/api/v1/menu-items/${item.body.id}`, { daily_limited: true });
    await api("POST", `/api/v1/menu-daily-stock/item/${item.body.id}/set`, {
      qty,
      expected_remaining: 0,
    });
    made[name] = item.body.id;
  }
  const cakeName = `戚風-${run}`;
  const sconeName = `司康-${run}`;

  await page.goto(`${BASE}/cash`, { waitUntil: "networkidle" });
  const cake = page.getByLabel(`${cakeName} 剩 3 份，記成報廢`);
  const scone = page.getByLabel(`${sconeName} 剩 1 份，記成報廢`);
  await cake.waitFor();
  ok("結帳區列出剩餘份數、預設全勾", (await cake.isChecked()) && (await scone.isChecked()));
  await scone.uncheck();
  await page.screenshot({ path: `${SHOTS}/leftover-01-close.png`, fullPage: true });

  const expected = await api("GET", "/api/v1/cash-sessions/current");
  await page.getByLabel("實點金額").fill("1000");
  await page.getByRole("button", { name: "結帳" }).click();
  await page.getByText("已結帳").waitFor();
  const notice = page.getByText(/已記成報廢：/);
  ok("結帳後告知已記成報廢", (await notice.textContent()).includes(`${cakeName} 3 份`));
  await page.screenshot({ path: `${SHOTS}/leftover-02-closed.png`, fullPage: true });

  const stock = await api("GET", "/api/v1/menu-daily-stock");
  const left = Object.fromEntries(stock.body.map((e) => [e.label, e.remaining]));
  ok("戚風記成報廢後為 0、司康不動仍剩 1", left[cakeName] === 0 && left[sconeName] === 1, JSON.stringify(left));
  const now = new Date();
  const from = new Date(now.getTime() - 3600_000).toISOString();
  const to = new Date(now.getTime() + 3600_000).toISOString();
  const report = await api(
    "GET",
    `/api/v1/reports/sales-margin?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,
  );
  const waste = (report.body?.food_waste_breakdown ?? []).find((r) => r.reason === "WASTE");
  ok("損耗報表有這次的報廢（至少 3 份、120 元）", waste !== undefined && waste.qty >= 3 && Number(waste.cost) >= 120, JSON.stringify(waste));
  void expected;
} catch (err) {
  ok("流程執行", false, String(err));
  await page.screenshot({ path: `${SHOTS}/leftover-error.png`, fullPage: true }).catch(() => {});
} finally {
  // 開一個新班別，讓之後的煙霧照常可以收現。
  await api("POST", "/api/v1/cash-sessions/open", { opening_float: "1000" }).catch(() => {});
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
