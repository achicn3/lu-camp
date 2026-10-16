// 續杯（店主 2026-10-16）真瀏覽器煙霧：
// ① 菜單管理：續杯折數設定、品項「可續杯」勾選。
// ② POS 點餐視窗勾「續杯」→ 單價照八折（加料也打折）、購物車行冠「續杯・」，和一般點分開。
// ③ 客人頁：可續杯品項標「可續杯 8 折」、視窗勾「我是續杯」→ 購物車續杯價；低消桌缺額不算續杯並加註。
// ④ 外帶送一張續杯＋一般點 → POS 線上訂單帶入結帳，續杯行照續杯價。
// 結束後取消測試單、還原品項與桌號設定並重新發佈。需要本機示範環境（後端 :8116、POS :3116、Worker :8794）。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1");
const API = "http://localhost:8116/api/v1", POS = "http://localhost:3116", ORDER = "http://localhost:8794";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/menu-refill-shots";
const ITEM = process.env.SMOKE_ITEM ?? "拿鐵"; // 基本價 150、溫度（熱／冰）、加購（燕麥奶 +20）
const TABLE = "煙霧續杯";
mkdirSync(SHOTS, { recursive: true });

let token = "";
async function api(method, path, body) {
  const r = await fetch(API + path, {
    method,
    headers: { Connection: "close", "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.ok(r.ok, `${method} ${path}: ${r.status} ${await r.clone().text()}`);
  return r.status === 204 ? null : r.json();
}
token = (await api("POST", "/auth/login", { username: "dev-manager", password: PASSWORD })).access_token;
const original = await api("GET", "/settings");
const restore = {
  dine_in_tables: original.dine_in_tables,
  dine_in_minimums: original.dine_in_minimums ?? {},
  menu_refill_pct: original.menu_refill_pct,
};
const item = (await api("GET", "/menu-items")).find((i) => i.name === ITEM);
assert.ok(item, `需要品項 ${ITEM}`);
const wasRefillable = item.refillable;

const wasAccepting = (await api("GET", "/online-orders")).accepting;
const browser = await chromium.launch();
let orderId;
try {
  await api("PATCH", "/settings", {
    menu_refill_pct: 80,
    dine_in_tables: [...original.dine_in_tables, TABLE],
    dine_in_minimums: { ...restore.dine_in_minimums, [TABLE]: { mode: "amount", amount: "300" } },
  });
  await api("PATCH", `/menu-items/${item.id}`, { refillable: false });

  // ① 菜單管理：勾「可續杯」
  const pos = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  await skipOpeningCheckRedirect(pos);
  await pos.goto(`${POS}/login`);
  await pos.locator('input[name="username"]').fill("dev-manager");
  await pos.locator('input[name="password"]').fill(PASSWORD);
  await pos.getByRole("button", { name: "登入", exact: true }).click();
  await pos.waitForURL((url) => !url.pathname.startsWith("/login"));
  await pos.goto(`${POS}/menu`);
  const pct = pos.getByLabel("續杯折數（%）");
  await pos.getByText("目前續杯打 8 折").waitFor();
  assert.equal(await pct.inputValue(), "80");
  await pos.getByRole("button", { name: `${ITEM} 編輯` }).click();
  const dialog = pos.getByRole("dialog", { name: `編輯 ${ITEM}` });
  await dialog.getByRole("checkbox", { name: /可續杯/ }).check();
  await pos.screenshot({ path: `${SHOTS}/01-menu-edit-refillable.png` });
  await dialog.getByRole("button", { name: "儲存" }).click();
  await dialog.waitFor({ state: "hidden" });
  const row = pos.locator("tr").filter({ has: pos.locator(".menu-item-name", { hasText: new RegExp(`^${ITEM}$`) }) });
  await row.getByText("可續杯", { exact: true }).waitFor();
  assert.equal((await api("GET", "/menu-items")).find((i) => i.id === item.id).refillable, true);
  await pos.screenshot({ path: `${SHOTS}/02-menu-list.png` });

  // ② POS 點餐：續杯 136（(150＋20)×80%），和一般點分兩行
  await pos.goto(`${POS}/pos`);
  const addCup = async (refill) => {
    await pos.locator(".pos-menu-tile").filter({ hasText: ITEM }).click();
    const pick = pos.getByRole("dialog", { name: `加入 ${ITEM}` });
    await pick.getByRole("radio", { name: "熱" }).check();
    await pick.getByRole("radio", { name: /燕麥奶/ }).check().catch(() => pick.getByRole("checkbox", { name: /燕麥奶/ }).check());
    if (refill) {
      await pick.getByRole("checkbox", { name: "續杯（8 折，加料也打折）" }).check();
      await pick.locator(".pos-qty-dialog-price").filter({ hasText: "136" }).waitFor();
      await pos.screenshot({ path: `${SHOTS}/03-pos-refill-dialog.png` });
    } else {
      await pick.locator(".pos-qty-dialog-price").filter({ hasText: "170" }).waitFor();
    }
    await pick.getByRole("button", { name: "加入購物車" }).click();
  };
  await addCup(false);
  await addCup(true);
  const refillLine = `續杯・${ITEM}（熱、燕麥奶）`;
  await pos.getByRole("button", { name: `移除 ${refillLine}` }).waitFor();
  await pos.getByRole("button", { name: `移除 ${ITEM}（熱、燕麥奶）` }).waitFor();
  const optionIds = item.option_groups.flatMap((g) => g.options)
    .filter((o) => o.name === "熱" || o.name === "燕麥奶").map((o) => o.id);
  const quote = await api("POST", "/sales/quote", {
    lines: [{ line_type: "MENU", menu_item_id: item.id, qty: 1, menu_option_ids: optionIds, menu_refill: true }],
  });
  assert.deepEqual([quote.lines[0].description, quote.lines[0].unit_price], [refillLine, "136"]);
  await pos.screenshot({ path: `${SHOTS}/04-pos-cart.png` });
  await pos.getByRole("button", { name: `移除 ${refillLine}` }).click();
  await pos.getByRole("button", { name: `移除 ${ITEM}（熱、燕麥奶）` }).click();

  // ③ 客人頁：低消 $300 的桌，一般 170＋續杯 136＝306，但續杯不算 → 還差 $130
  await api("POST", "/online-order/publish");
  const code = (await api("GET", "/online-order/status")).tables.find((t) => t.label === TABLE).code;
  const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const guestCup = async (refill) => {
    await guest.getByRole("button", { name: "飲品", exact: true }).click();
    const card = guest.locator(".item").filter({ hasText: ITEM });
    await card.getByText("可續杯 8 折").waitFor();
    await card.locator(".item-add").click();
    const sheet = guest.locator("#sheet");
    await sheet.getByRole("radio", { name: "熱", exact: true }).check();
    await sheet.getByRole("radio", { name: /燕麥奶/ }).check();
    if (refill) {
      await sheet.getByLabel("我是續杯（8 折，加料也打折）").check();
      await guest.screenshot({ path: `${SHOTS}/05-guest-refill-sheet.png` });
    }
    await guest.locator("#detail-add").click();
  };
  await guest.goto(`${ORDER}/t/${code}`);
  await guestCup(false);
  await guestCup(true);
  await guest.getByRole("button", { name: /購物車 2 份/ }).click();
  await guest.locator(".cart-line").filter({ hasText: refillLine }).filter({ hasText: "$136" }).waitFor();
  await guest.locator(".cart-total").filter({ hasText: "$306" }).waitFor();
  await guest.getByText("本桌低消 $300，還差 $130（續杯不計入低消）。").waitFor();
  assert.ok(await guest.getByRole("button", { name: "送出訂單", exact: true }).isDisabled());
  assert.ok(await guest.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await guest.screenshot({ path: `${SHOTS}/06-guest-cart-minimum.png` });

  // ④ 外帶送單 → POS 帶入結帳照續杯價（暫時開放接單，結束後還原）
  await api("PUT", "/online-orders/accepting", { accepting: true });
  await guest.evaluate(() => localStorage.clear());
  await guest.goto(ORDER);
  await guestCup(true);
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  const resultPromise = guest.waitForResponse(
    (r) => r.url() === `${ORDER}/api/orders` && r.request().method() === "POST", { timeout: 90000 },
  );
  await guest.getByRole("button", { name: "送出訂單", exact: true }).click();
  const result = await resultPromise;
  assert.equal(result.status(), 201);
  assert.equal(result.request().postDataJSON().lines[0].refill, true);
  assert.equal((await result.json()).total, 136);
  let received;
  for (let n = 0; n < 40 && !received; n++) {
    received = (await api("GET", "/online-orders")).orders.find(
      (o) => o.sync_status !== "VOIDED" && o.lines.some((l) => l.refill && l.name === refillLine),
    );
    if (!received) await new Promise((resolve) => setTimeout(resolve, 500));
  }
  assert.ok(received, "refill order must reach POS");
  orderId = received.id;
  await pos.goto(`${POS}/pos`);
  await pos.getByRole("button", { name: /線上訂單/ }).click();
  const card = pos.locator(".online-order").filter({ hasText: refillLine }).first();
  await card.getByRole("button", { name: "帶入結帳" }).click();
  await pos.getByRole("button", { name: "取消帶入" }).waitFor();
  await pos.getByRole("button", { name: `移除 ${refillLine}` }).waitFor();
  await pos.screenshot({ path: `${SHOTS}/07-pos-online-loaded.png` });
  const cart = await api("GET", `/online-orders/${orderId}/cart`);
  assert.deepEqual(cart.lines.map((l) => [l.description, l.unit_price, l.menu_refill]), [[refillLine, "136", true]]);
  await pos.getByRole("button", { name: "取消帶入" }).click();
  console.log("PASS refill setting + toggle → POS refill 136 separate line → guest refill badge/sheet/cart, minimum excludes refill → takeout refill order loads into POS at 136");
} finally {
  if (orderId) await api("POST", `/online-orders/${orderId}/cancel`).catch(() => {});
  if (!wasAccepting) await api("PUT", "/online-orders/accepting", { accepting: false }).catch(() => {});
  await api("PATCH", `/menu-items/${item.id}`, { refillable: wasRefillable });
  await api("PATCH", "/settings", restore);
  await api("POST", "/online-order/publish");
  await browser.close();
}
