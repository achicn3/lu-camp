// 掃碼直接進完整菜單（店主 2026-10-10；原 M1b 首頁煙霧改寫）：真 POS 資料 → 發佈 → 手機一進來就是「店員推薦」分頁
// （店主排的順序）→ 切分類／選項／購物車／上一頁下一頁／即時售完。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1");
const API = process.env.SMOKE_API ?? "http://localhost:8104";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8789";
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/menu-home");
mkdirSync(shots, { recursive: true });
let token = "";
async function api(method, path, body) {
  const response = await fetch(`${API}/api/v1${path}`, { method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: body === undefined ? undefined : JSON.stringify(body) });
  assert.ok(response.ok, `${path}: ${response.status} ${await response.clone().text()}`);
  return response.status === 204 ? null : response.json();
}
token = (await api("POST", "/auth/login", { username: process.env.SMOKE_USERNAME ?? "dev-manager", password: process.env.SMOKE_PASSWORD ?? "dev-test-123456" })).access_token;
const run = randomUUID().slice(0, 6);
const category = `手沖咖啡與今日甜點-${run}`;
const items = [];
for (let index = 0; index < 5; index++) {
  const item = await api("POST", "/menu-items", { name: `${["蜜桃花香手沖咖啡", "濃郁可可拿鐵", "今天的手作甜點", "風味咖啡", "售完甜點"][index]}-${run}`, category, unit_price: "150", unit_cost: "50", sort_order: -99999 + index });
  await api("PUT", `/online-order/menu-items/${item.id}/presentation`, { flavor_description: "蜜桃・花香・甜感", audience_description: "適合喜歡果香與明亮酸甜的人" });
  await api("PATCH", `/menu-items/${item.id}`, { daily_limited: true });
  await api("POST", `/menu-daily-stock/item/${item.id}/set`, { qty: index === 4 ? 0 : 5, expected_remaining: 0 });
  items.push(item);
}
const group = await api("POST", "/menu-option-groups", { name: `溫度-${run}`, min_select: 1, max_select: 1, options: [{ name: "熱", price_delta: "0" }, { name: "冰", price_delta: "10" }] });
await api("PUT", `/menu-items/${items[1].id}/option-groups`, { group_ids: [group.id] });
// 店員推薦：故意倒過來排（第 3、1、2 個），客人看到的要照這個順序
const originalPicks = await api("GET", "/online-order/staff-picks");
await api("PUT", "/online-order/staff-picks", { items: [2, 0, 1].map((index) => ({ kind: "item", id: items[index].id })) });
await api("POST", "/online-order/publish");
const table = (await api("GET", "/online-order/status")).tables.find((entry) => entry.label === "A1");
assert.ok(table, "A1 must be seeded by online-guest-pos smoke");
const snapshot = await (await fetch(`${ORDER}/api/menu`)).json();
const expected = snapshot.picks.filter((pick) => pick.kind === "item").map((pick) => String(pick.id));
const pressed = (phone) => phone.locator('#tabs button[aria-pressed="true"]');
const browser = await chromium.launch();
const errors = [];
try {
  for (const width of [375, 390, 430]) {
    const phone = await browser.newPage({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true });
    phone.on("pageerror", (error) => errors.push(String(error)));
    await phone.goto(`${ORDER}/t/${table.code}`, { waitUntil: "networkidle" });
    assert.equal(await pressed(phone).innerText(), "店員推薦", "一進來就打開店員推薦");
    assert.equal(await phone.locator("#tabs button").first().innerText(), "店員推薦");
    assert.deepEqual(await phone.locator("#list .item").evaluateAll((nodes) => nodes.map((node) => node.dataset.itemId)),
      expected.slice(0, 3), "照店主排的順序");
    await phone.getByRole("heading", { name: "在露坑坐一下。" }).waitFor();
    assert.equal(await phone.locator("#splash").count(), 0);
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await phone.screenshot({ path: join(shots, `home-${width}.png`), fullPage: true });
    await phone.locator("#tabs").getByRole("button", { name: category, exact: true }).click();
    const card = (index) => phone.locator(`#list .item[data-item-id="${items[index].id}"]`);
    await card(0).getByRole("button", { name: `加入：${items[0].name}`, exact: true }).click();
    await card(0).getByRole("button", { name: `加入：${items[0].name}`, exact: true }).click();
    await card(1).getByRole("button", { name: `選擇選項：${items[1].name}`, exact: true }).click();
    await phone.getByRole("button", { name: "加入購物車", exact: true }).click();
    await phone.getByText("請依每組規則選好選項。").waitFor();
    await phone.getByLabel("冰", { exact: false }).check();
    await phone.getByRole("button", { name: "加入購物車", exact: true }).click();
    assert.equal(await card(4).getByRole("button", { name: `今日售完：${items[4].name}`, exact: true }).isDisabled(), true);
    await phone.getByRole("button", { name: /購物車 3 份/ }).click();
    await phone.getByText("合計 $460", { exact: true }).waitFor();
    await phone.screenshot({ path: join(shots, `cart-${width}.png`), fullPage: true });
    await phone.getByRole("button", { name: "← 返回菜單", exact: true }).click();
    await pressed(phone).getByText(category, { exact: true }).waitFor();
    assert.equal(new URL(phone.url()).pathname, `/t/${table.code}`);
    await phone.goBack();
    await pressed(phone).getByText("店員推薦", { exact: true }).waitFor();
    await phone.goForward();
    await pressed(phone).getByText(category, { exact: true }).waitFor();
    await phone.reload({ waitUntil: "networkidle" });
    await pressed(phone).getByText(category, { exact: true }).waitFor();
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await phone.screenshot({ path: join(shots, `category-${width}.png`), fullPage: true });
    if (width === 430) {
      await card(0).locator(".item-detail").click();
      await phone.locator(".qty-input").fill("2");
      await api("POST", `/menu-daily-stock/item/${items[0].id}/adjust`, { delta: -5, reason: "WASTE" });
      await phone.waitForFunction((id) => document.querySelector(`#list .item[data-item-id="${id}"] .item-add`)?.disabled, items[0].id, { timeout: 35000 });
      assert.equal(await phone.locator("#detail-add").isDisabled(), true);
      assert.equal(await phone.locator(".qty-input").inputValue(), "2");
      await phone.getByRole("button", { name: "關閉", exact: true }).click();
      assert.equal(await phone.evaluate(() => document.activeElement?.classList.contains("item-detail")), true);
      await phone.getByRole("button", { name: /購物車 3 份/ }).click();
      const invalid = phone.locator(".cart-line", { hasText: items[0].name });
      await invalid.getByRole("button", { name: "移除", exact: true }).click();
      await phone.getByText("合計 $160", { exact: true }).waitFor();
      assert.equal(await phone.locator(".cart-line").count(), 1, "Keep the unaffected choice");
      await phone.getByRole("button", { name: "← 返回菜單", exact: true }).click();
      await card(0).locator(".item-detail").click();
      assert.equal(await phone.locator("#detail-add").isDisabled(), true);
      await api("POST", `/menu-daily-stock/item/${items[0].id}/set`, { qty: 1, expected_remaining: 0 });
      await phone.waitForFunction(() => document.querySelector("#detail-add")?.disabled === false, undefined, { timeout: 35000 });
      await phone.getByRole("button", { name: "關閉", exact: true }).click();
      assert.equal(await phone.evaluate(() => document.activeElement?.classList.contains("item-detail")), true);
    }
    await phone.close();
  }
  const remaining = (await api("GET", "/menu-items")).find((item) => item.id === items[1].id).remaining;
  assert.equal(remaining, 5, "Cart selections must not reserve POS stock");
  assert.deepEqual(errors, []);
  console.log(`PASS full-menu/staff-picks/options/cart/QR/history/live-stock, 375/390/430px. Screenshots: ${shots}`);
} finally {
  await browser.close();
  await api("PUT", "/online-order/staff-picks", originalPicks).catch(() => {});
}
