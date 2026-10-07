// 線上點餐電子菜單瀏覽器煙霧（docs/44 §3.5、§4.2；O3）：
// POS 菜單頁按「發佈到線上點餐」→ 菜單、照片、手寫字型子集、桌位碼推到雲端（本機 wrangler dev）
// → 手機尺寸打開 /t/<桌位碼>：先是首頁，點「全部」查看完整菜單（桌號、問候、分類、售完、選項）
// → 失效的桌位碼顯示說明 → 公開菜單裡沒有成本。
//
// 需三個服務已起且指向隔離測試庫（SMOKE_ALLOW_WRITE=1）：
//   backend（ONLINE_ORDER_BASE_URL 指向 SMOKE_ORDER、密鑰與 wrangler 的 .dev.vars 相同）、frontend、wrangler dev。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium, devices } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const ORDER = process.env.SMOKE_ORDER ?? "http://localhost:8787";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立品項並發佈到線上，請指向隔離測試庫");
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
const desk = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
desk.on("pageerror", (err) => ok("POS 頁面 JS 錯誤", false, String(err)));

try {
  await skipOpeningCheckRedirect(desk);
  await desk.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await desk.waitForTimeout(400);
  await desk.fill('input[name="username"]', "dev-manager");
  await desk.fill('input[name="password"]', "dev-test-123456");
  await desk.click('button:has-text("登入")');
  await desk.waitForURL((url) => !url.pathname.startsWith("/login"));
  token = await desk.evaluate(() => localStorage.getItem("lu-camp.access-token"));

  // 準備：兩道菜（一道帶選項、一道今日售完）＋桌號 A1
  const latte = await api("POST", "/api/v1/menu-items", {
    name: `拿鐵-${run}`,
    unit_price: "150",
    unit_cost: "41",
    category: "咖啡",
    description: "濃縮咖啡加鮮奶，可換燕麥奶",
  });
  const group = await api("POST", "/api/v1/menu-option-groups", {
    name: `溫度-${run}`,
    min_select: 1,
    max_select: 1,
    options: [
      { name: "熱", price_delta: "0" },
      { name: "冰", price_delta: "0" },
    ],
  });
  await api("PUT", `/api/v1/menu-items/${latte.body.id}/option-groups`, { group_ids: [group.body.id] });
  const cake = await api("POST", "/api/v1/menu-items", {
    name: `戚風-${run}`,
    unit_price: "90",
    category: "甜點",
  });
  await api("PATCH", `/api/v1/menu-items/${cake.body.id}`, { daily_limited: true });
  const settings = await api("GET", "/api/v1/settings");
  const tables = new Set([...(settings.body.dine_in_tables ?? []), "A1"]);
  await api("PATCH", "/api/v1/settings", { dine_in_tables: [...tables] });

  // 1. POS 菜單頁按發佈
  await desk.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  await desk.getByRole("tab", { name: "線上發布", exact: true }).click();
  const panel = desk.getByRole("region", { name: "線上點餐" });
  await panel.getByRole("button", { name: "發佈到線上點餐" }).click();
  const notice = panel.getByText(/已發佈 \d+ 道菜/);
  await notice.waitFor({ timeout: 60000 });
  ok("POS 發佈成功", true, await notice.textContent());
  await panel.screenshot({ path: `${SHOTS}/online-01-publish-panel.png` });

  const status = await api("GET", "/api/v1/online-order/status");
  const a1 = status.body.tables.find((t) => t.label === "A1");
  ok("狀態列出 A1 的網址", Boolean(a1?.url), a1?.url);

  // 2. 公開菜單不含成本
  const menu = await (await fetch(`${ORDER}/api/menu`)).json();
  ok("公開菜單沒有成本欄位", !JSON.stringify(menu).includes("cost"));
  ok("公開菜單有手寫字型子集", typeof menu.font === "string" && menu.font.length === 64);

  // 3. 客人手機打開
  const phoneCtx = await browser.newContext({ ...devices["iPhone 13"] });
  const phone = await phoneCtx.newPage();
  phone.on("pageerror", (err) => ok("點餐頁 JS 錯誤", false, String(err)));
  const csp = [];
  phone.on("console", (msg) => {
    if (/Content Security Policy/i.test(msg.text())) csp.push(msg.text());
  });
  await phone.goto(a1.url.replace(/^https?:\/\/[^/]+/, ORDER));
  await phone.locator("#menu-home").waitFor();
  ok("一打開即顯示露坑首頁", await phone.locator("#menu-home").isVisible());
  await phone.screenshot({ path: `${SHOTS}/online-02-home.png` });
  ok("顯示桌號", (await phone.locator("#table").textContent()) === "桌 A1");
  const greet = await phone.locator("#greeting").textContent();
  ok("顯示時段問候", /^(早安|午安|晚安)，/.test(greet ?? ""), greet);
  await phone.waitForFunction(() => document.documentElement.classList.contains("hand-font-ready"), null, {
    timeout: 10000,
  });
  ok("手寫字型子集載入", true);
  const all = phone.locator("#tabs").getByRole("button", { name: "全部", exact: true });
  await all.click();
  ok("點全部顯示完整菜單", (await all.getAttribute("aria-pressed")) === "true");
  const cakeRow = phone.locator("#list .item", { hasText: `戚風-${run}` });
  await cakeRow.locator(".item-badge-off").waitFor();
  ok("每日限量沒填份數＝今日售完", (await cakeRow.locator(".item-badge-off").textContent()) === "今日售完");
  ok("售完不能加入", await cakeRow.locator(".item-add").isDisabled());
  await phone.screenshot({ path: `${SHOTS}/online-03-menu.png` });

  await phone.locator("#list .item", { hasText: `拿鐵-${run}` }).locator(".item-detail").click();
  const sheet = phone.getByRole("dialog");
  await sheet.waitFor();
  ok("點品項看到選項", (await sheet.textContent()).includes(`溫度-${run}`));
  await phone.screenshot({ path: `${SHOTS}/online-04-detail.png` });
  ok("沒有違反安全標頭（CSP）", csp.length === 0, csp.join(" | "));

  // 4. 失效的桌位碼
  await phone.goto(`${ORDER}/t/${"x".repeat(22)}`);
  await phone.locator("#message").waitFor();
  ok("失效的 QR 顯示說明", (await phone.locator("#message").textContent()).includes("失效"));
  await phoneCtx.close();
} catch (err) {
  ok("流程執行", false, String(err));
  await desk.screenshot({ path: `${SHOTS}/online-error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
