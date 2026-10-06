// M1a: 真 backend/Postgres → 管理者設定 → Worker 發布 → 375/390/430px 客人頁。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1");
const API = process.env.SMOKE_API ?? "http://localhost:8104";
const BASE = process.env.SMOKE_BASE ?? "http://localhost:3104";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8789";
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/menu-presentation");
mkdirSync(shots, { recursive: true });
let token = "";
async function api(method, path, body) {
  const response = await fetch(`${API}/api/v1${path}`, { method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: body === undefined ? undefined : JSON.stringify(body) });
  assert.ok(response.ok, `${path}: ${response.status} ${await response.clone().text()}`);
  return response.status === 204 ? null : response.json();
}
const run = randomUUID().slice(0, 6);
const browser = await chromium.launch();
const desk = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = [];
desk.on("pageerror", (error) => errors.push(String(error)));
try {
  await skipOpeningCheckRedirect(desk);
  await desk.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await desk.locator('input[name="username"]').fill(process.env.SMOKE_USERNAME ?? "dev-manager");
  await desk.locator('input[name="password"]').fill(process.env.SMOKE_PASSWORD ?? "dev-test-123456");
  await desk.getByRole("button", { name: "登入", exact: true }).click();
  await desk.waitForURL((url) => !url.pathname.startsWith("/login"));
  token = await desk.evaluate(() => localStorage.getItem("lu-camp.access-token"));
  const category = `咖啡-${run}`;
  async function create(label, stock, sort_order) {
    const item = await api("POST", "/menu-items", { name: `${label}-${run}`, unit_price: "150", unit_cost: "50", category, sort_order });
    await api("PATCH", `/menu-items/${item.id}`, { daily_limited: true });
    await api("POST", `/menu-daily-stock/item/${item.id}/set`, { qty: stock, expected_remaining: 0 });
    return item;
  }
  const sold = await create("已售完", 0, -5);
  const low = await create("蜜桃咖啡", 3, 1);
  const high = await create("供應充足", 36, 2);
  const hidden = await create("隱藏售完", 0, 3);
  await api("PUT", `/online-order/menu-items/${hidden.id}/presentation`, { hide_sold_out: true });
  await desk.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  await desk.getByRole("button", { name: `${low.name} 線上呈現`, exact: true }).click();
  let dialog = desk.getByRole("dialog", { name: `${low.name} 的線上呈現` });
  await dialog.getByLabel("風味描述", { exact: true }).fill("蜜桃・花香・甜感");
  await dialog.getByLabel("適合族群", { exact: true }).fill("適合喜歡明亮果香的人");
  await dialog.getByLabel("露坑推薦", { exact: true }).check();
  await dialog.getByLabel("新品 NEW", { exact: true }).check();
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  await dialog.getByLabel("今日限定日期", { exact: true }).fill(day);
  await dialog.getByLabel("低庫存顯示門檻", { exact: true }).fill("3");
  await dialog.screenshot({ path: join(shots, "admin.png") });
  await dialog.getByRole("button", { name: "儲存設定" }).click();
  await dialog.waitFor({ state: "detached" });
  await desk.reload({ waitUntil: "networkidle" });
  await desk.getByRole("button", { name: `${low.name} 線上呈現`, exact: true }).click();
  dialog = desk.getByRole("dialog", { name: `${low.name} 的線上呈現` });
  await dialog.getByLabel("風味描述", { exact: true }).waitFor();
  assert.equal(await dialog.getByLabel("風味描述", { exact: true }).inputValue(), "蜜桃・花香・甜感");
  assert.equal(await dialog.getByLabel("低庫存顯示門檻", { exact: true }).inputValue(), "3");
  await dialog.getByRole("button", { name: "關閉", exact: true }).click();
  await desk.getByText("菜單排序", { exact: true }).click();
  await desk.getByLabel(`${high.name} 商品排序`, { exact: true }).fill("-2");
  await desk.getByRole("button", { name: `儲存${high.name}商品排序`, exact: true }).click();
  const panel = desk.getByRole("region", { name: "線上點餐" });
  await panel.getByRole("button", { name: "發佈到線上點餐" }).click();
  await panel.getByText(/已發佈 \d+ 道菜/).waitFor({ timeout: 60000 });
  const snapshot = await (await fetch(`${ORDER}/api/menu`)).json();
  const published = snapshot.items.find((item) => item.id === low.id);
  assert.equal(published.remaining, 3);
  assert.equal(published.unit_price, 150);
  assert.equal(published.presentation.flavor_description, "蜜桃・花香・甜感");
  assert.equal(JSON.stringify(snapshot).includes("unit_cost"), false);
  for (const width of [375, 390, 430]) {
    const phone = await browser.newPage({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true });
    phone.on("pageerror", (error) => errors.push(String(error)));
    if (width === 390) await phone.clock.install({ time: new Date() });
    await phone.goto(ORDER, { waitUntil: "networkidle" });
    await phone.getByRole("tab", { name: category, exact: true }).click();
    const lowCard = phone.locator(".item", { hasText: low.name });
    await lowCard.getByText("今天剩 3 份", { exact: true }).waitFor();
    await lowCard.getByText("蜜桃・花香・甜感", { exact: true }).waitFor();
    assert.match(await lowCard.innerText(), /露坑推薦.*新品.*今日限定/);
    assert.equal(await phone.locator(".item", { hasText: high.name }).getByText(/剩/).count(), 0);
    assert.equal(await phone.locator(".item", { hasText: hidden.name }).count(), 0);
    const names = await phone.locator(".item-name").allTextContents();
    assert.deepEqual(names, [high.name, low.name, sold.name]);
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await phone.screenshot({ path: join(shots, `guest-${width}.png`), fullPage: true });
    if (width === 390) {
      await lowCard.click();
      await phone.locator(".qty-input").fill("2");
      const tomorrow = new Date(`${day}T12:00:00+08:00`);
      tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
      await phone.clock.setFixedTime(tomorrow);
      await phone.clock.runFor(16000);
      await phone.waitForFunction(() => !document.querySelector("#sheet-body .item-labels")?.textContent.includes("今日限定"));
      assert.equal(await phone.locator(".qty-input").inputValue(), "2", "Cross-day labels must not reset the selection");
      await phone.getByRole("button", { name: "關閉", exact: true }).click();
    }
    await phone.locator(".item", { hasText: sold.name }).click();
    assert.equal(await phone.getByRole("button", { name: "加入購物車", exact: true }).count(), 0);
    await phone.close();
  }
  const unchanged = (await api("GET", "/menu-items")).find((item) => item.id === low.id);
  assert.equal(unchanged.remaining, 3); assert.equal(unchanged.unit_cost, "50");
  assert.deepEqual(errors, []);
  console.log(`PASS settings reload, publish, stock policy, sorting, sold-out filtering and 375/390/430px. Screenshots: ${shots}`);
} catch (error) {
  await desk.screenshot({ path: join(shots, "error.png"), fullPage: true });
  throw error;
} finally { await browser.close(); }
