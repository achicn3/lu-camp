// 店員推薦＋掃碼直接進完整菜單煙霧（店主 2026-10-10）：後台「線上發布」→ 店員推薦清單加入餐飲、手沖體驗卡、
// 帶著走商品並調順序 → 發佈 → 客人手機一進來就是「店員推薦」分頁、照排好的順序、三種卡都在；
// 分類列：店員推薦、手沖體驗、各分類、帶著走；「不知道喝什麼？」是問候語下的小按鈕。結束時還原清單、清掉建的東西。
// 需 backend :8114（含線上點餐設定）＋ wrangler :8799 ＋ frontend :3500。只准對隔離測試環境執行（SMOKE_ALLOW_WRITE=1）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium, devices } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const BASE = process.env.SMOKE_BASE ?? "http://localhost:3500";
const API = process.env.SMOKE_API ?? "http://localhost:8114";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8799";
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/online-staff-picks");
mkdirSync(SHOTS, { recursive: true });

let token = "";
async function api(method, path, body, headers = {}) {
  const response = await fetch(`${API}/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}
async function must(method, path, body, headers) {
  const result = await api(method, path, body, headers);
  assert.ok(result.status < 300, `${method} ${path}: ${result.status} ${JSON.stringify(result.body)}`);
  return result.body;
}
const results = [];
const ok = (name, pass, detail = "") => {
  results.push(pass);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
};

token = (await must("POST", "/auth/login", { username: USERNAME, password: PASSWORD })).access_token;
const run = randomUUID().slice(0, 6);
const latte = await must("POST", "/menu-items", { name: `推薦拿鐵-${run}`, unit_price: "150", unit_cost: "30", category: `咖啡-${run}` });
const card = await must("POST", "/online-order/experiences", {
  menu_item_id: latte.id, option_ids: [], title: `推薦體驗-${run}`, includes: [], theme: "peach", art: "peach", effect: "random",
});
const bean = await must("POST", "/catalog-products", { name: `推薦豆-${run}`, unit_price: "450", sku: `PICK-${run}` });
const stocktake = await must("POST", "/stocktakes");
await must("POST", `/stocktakes/${stocktake.id}/confirm`, { counts: [{ catalog_product_id: bean.id, counted_qty: 3 }] });
const listing = await must("POST", "/online-order/retail", { catalog_product_id: bean.id });
const original = await must("GET", "/online-order/staff-picks");
await must("PUT", "/online-order/staff-picks", { items: [] });
const settings = await must("GET", "/settings");
await must("PATCH", "/settings", { dine_in_tables: [...new Set([...(settings.dine_in_tables ?? []), "A1"])] });

const browser = await chromium.launch();
const desk = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
const errors = [];
desk.on("pageerror", (e) => errors.push(`後台：${e}`));
try {
  await skipOpeningCheckRedirect(desk, BASE);
  await desk.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await desk.fill('input[name="username"]', USERNAME);
  await desk.fill('input[name="password"]', PASSWORD);
  await desk.click('button:has-text("登入")');
  await desk.waitForURL(`${BASE}/`);
  await desk.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  await desk.getByRole("tab", { name: "線上發布" }).click();
  const picker = desk.getByLabel("加入店員推薦");
  await picker.selectOption({ label: latte.name });
  await picker.selectOption({ label: `手沖體驗：${card.title}` });
  await picker.selectOption({ label: `帶著走：${bean.name}` });
  await desk.getByRole("button", { name: `上移 帶著走：${bean.name}` }).click();
  await desk.getByRole("button", { name: `上移 帶著走：${bean.name}` }).click();
  const listNames = await desk.getByRole("list", { name: "店員推薦清單" }).locator(".staff-pick-name").allTextContents();
  ok("後台排序：帶著走移到第一", listNames.join("|") === [`帶著走：${bean.name}`, latte.name, `手沖體驗：${card.title}`].join("|"), listNames.join("|"));
  await desk.getByRole("button", { name: "儲存店員推薦" }).click();
  await desk.getByText(/已儲存。到上方按/).waitFor();
  await desk.getByRole("region", { name: "店員推薦" }).screenshot({ path: join(SHOTS, "01-admin.png") });
  ok("店員推薦存好", true);

  await must("POST", "/online-order/publish");
  const code = (await must("GET", "/online-order/status")).tables.find((t) => t.label === "A1")?.code;
  await must("PUT", "/online-orders/accepting", { accepting: true });

  const guestCtx = await browser.newContext({ ...devices["iPhone 13"] });
  const guest = await guestCtx.newPage();
  guest.on("pageerror", (e) => errors.push(`客人頁：${e}`));
  await guest.goto(`${ORDER}/t/${code}`, { waitUntil: "networkidle" });
  const tabs = await guest.locator("#tabs button").allTextContents();
  ok("分類列：店員推薦第一、手沖體驗第二、帶著走最後", tabs[0] === "店員推薦" && tabs[1] === "手沖體驗" && tabs.at(-1) === "帶著走", tabs.join("｜"));
  ok("一進來就打開店員推薦", (await guest.locator('#tabs button[aria-pressed="true"]').innerText()) === "店員推薦");
  ok("問候語還在、「不知道喝什麼？」是小按鈕", (await guest.locator(".welcome-title").count()) === 1);
  const listText = await guest.locator("#list").innerText();
  const order = [bean.name, latte.name, card.title].map((name) => listText.indexOf(name));
  ok("店員推薦照後台排的順序、三種東西都在", order.every((i) => i >= 0) && order[0] < order[1] && order[1] < order[2], order.join(","));
  const cardWidth = await guest.locator("#list .brew-mini").first().evaluate((el) => el.getBoundingClientRect().width / innerWidth);
  ok("店員推薦裡的體驗卡和手沖體驗分頁一樣是半寬卡片（不撐滿螢幕）", cardWidth < 0.6, cardWidth.toFixed(2));
  await guest.screenshot({ path: join(SHOTS, "02-guest-landing.png") });
  await guest.locator("#tabs").getByRole("button", { name: "帶著走", exact: true }).click();
  ok("帶著走分頁有這包豆子", (await guest.locator("#list").innerText()).includes(bean.name));
  const overflow = await guest.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  ok("手機不橫向捲動", overflow <= 0, `溢出 ${overflow}px`);
  ok("頁面無 JS 例外", errors.length === 0, errors.join(" / "));
} catch (error) {
  await desk.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true }).catch(() => {});
  ok("流程跑完", false, String(error));
} finally {
  await api("PUT", "/online-order/staff-picks", original);
  await api("DELETE", `/online-order/retail/${listing.id}`);
  await api("DELETE", `/online-order/experiences/${card.id}`);
  await api("DELETE", `/menu-items/${latte.id}`);
  await api("POST", "/online-order/publish");
  await browser.close();
}
const failed = results.filter((r) => !r).length;
console.log(`\n${results.length - failed}/${results.length} PASS；截圖 ${SHOTS}`);
process.exit(failed === 0 ? 0 : 1);
