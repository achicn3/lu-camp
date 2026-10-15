// 桌號低消（2026-10-14 店主）真瀏覽器煙霧：POS 設定每桌低消 → 發佈 → 客人掃桌碼點餐被擋、顯示還差多少
// → 每人至少一份的桌填人數、點夠後送單 → POS 看到「桌號・幾位」。結束後取消測試單、還原桌號設定並重新發佈。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1");
const API = "http://localhost:8116/api/v1", POS = "http://localhost:3116", ORDER = "http://localhost:8794";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-table-minimum-shots";
const AMOUNT_TABLE = "煙霧低消", PERSON_TABLE = "煙霧每人";
mkdirSync(SHOTS, { recursive: true });

let token = "";
async function api(method, path, body) {
  const r = await fetch(API + path, {
    method,
    headers: { Connection: "close", "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.ok(r.ok, `${method} ${path}: ${r.status} ${await r.clone().text()}`);
  return r.json();
}
token = (await api("POST", "/auth/login", { username: "dev-manager", password: PASSWORD })).access_token;
const original = await api("GET", "/settings");
const restore = { dine_in_tables: original.dine_in_tables, dine_in_minimums: original.dine_in_minimums ?? {} };

const browser = await chromium.launch();
let orderId;
try {
  await api("PATCH", "/settings", { dine_in_tables: [...original.dine_in_tables, AMOUNT_TABLE, PERSON_TABLE] });

  // ① POS 設定頁：兩桌各設一種低消
  const pos = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  await skipOpeningCheckRedirect(pos);
  await pos.goto(`${POS}/login`);
  await pos.locator('input[name="username"]').fill("dev-manager");
  await pos.locator('input[name="password"]').fill(PASSWORD);
  await pos.getByRole("button", { name: "登入", exact: true }).click();
  await pos.waitForURL((url) => !url.pathname.startsWith("/login"));
  await pos.goto(`${POS}/settings`);
  await pos.getByLabel(`${AMOUNT_TABLE} 低消`).selectOption("amount");
  await pos.getByLabel(`${AMOUNT_TABLE} 最低金額`).fill("300");
  await pos.getByLabel(`${PERSON_TABLE} 低消`).selectOption("per_person_item");
  await pos.getByRole("button", { name: "儲存餐飲內用設定" }).click();
  await pos.getByText("餐飲內用設定已儲存").waitFor();
  await pos.locator(".dinein-card").screenshot({ path: `${SHOTS}/settings-minimums.png` });
  const saved = await api("GET", "/settings");
  assert.deepEqual(saved.dine_in_minimums, {
    ...restore.dine_in_minimums,
    [AMOUNT_TABLE]: { mode: "amount", amount: "300" },
    [PERSON_TABLE]: { mode: "per_person_item" },
  });

  // ② 發佈到線上點餐，取得兩桌的桌碼
  await api("POST", "/online-order/publish");
  const status = await api("GET", "/online-order/status");
  const code = (label) => status.tables.find((t) => t.label === label).code;

  // ③ 最低金額桌：點一份 $120 → 顯示還差 $180、送出鈕停用
  const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await guest.goto(`${ORDER}/t/${code(AMOUNT_TABLE)}`);
  await guest.getByRole("button", { name: "甜點", exact: true }).click();
  await guest.locator(".item").filter({ hasText: "巴斯克蛋糕" }).locator(".item-add").click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  await guest.getByText("本桌低消 $300，每張訂單需滿 $300 才能送出。").waitFor();
  await guest.getByText("本桌低消 $300，還差 $180。").waitFor();
  assert.ok(await guest.getByRole("button", { name: "送出訂單", exact: true }).isDisabled());
  assert.ok(await guest.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await guest.screenshot({ path: `${SHOTS}/guest-amount-short.png` });
  await guest.evaluate(() => { localStorage.clear(); sessionStorage.clear(); });

  // ④ 每人至少一份桌：填 2 位、只點 1 份 → 還差 1 份；再點 1 份 → 可送出 → 201
  await guest.goto(`${ORDER}/t/${code(PERSON_TABLE)}`);
  await guest.getByRole("button", { name: "甜點", exact: true }).click();
  await guest.locator(".item").filter({ hasText: "巴斯克蛋糕" }).locator(".item-add").click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  await guest.getByText("請先填寫用餐人數。").waitFor();
  await guest.getByLabel("用餐人數").fill("2");
  await guest.getByText("2 位用餐，至少需點 2 份餐點，還差 1 份。").waitFor();
  await guest.screenshot({ path: `${SHOTS}/guest-person-short.png` });
  await guest.locator(".cart-line").first().getByRole("button", { name: "+" }).click();
  const submit = guest.getByRole("button", { name: "送出訂單", exact: true });
  assert.ok(await submit.isEnabled());
  const resultPromise = guest.waitForResponse(
    (r) => r.url() === `${ORDER}/api/orders` && r.request().method() === "POST", { timeout: 90000 },
  );
  await submit.click();
  const result = await resultPromise;
  assert.equal(result.status(), 201);
  await guest.waitForURL(/\/order\//);
  const created = await result.json();
  const publicOrder = await (await fetch(`${ORDER}/api/orders/${created.token}`)).json();

  // ⑤ POS 收到單，桌號旁顯示人數
  let received;
  for (let n = 0; n < 40 && !received; n++) {
    // 以這張單的建立時間比對（重跑時舊的測試單也是同桌同人數）。
    received = (await api("GET", "/online-orders")).orders.find(
      (o) => o.table_label === PERSON_TABLE && Date.parse(o.created_at) === Date.parse(publicOrder.created_at),
    );
    if (!received) await new Promise((resolve) => setTimeout(resolve, 500));
  }
  assert.ok(received, "order with party size must reach POS");
  orderId = received.id;
  assert.equal(received.party_size, 2);
  await pos.goto(`${POS}/pos`);
  await pos.getByRole("button", { name: /線上訂單/ }).click();
  await pos.locator(".online-order").filter({ hasText: "待付款" }).getByText(`桌號 ${PERSON_TABLE}・2 位`).waitFor();
  await pos.screenshot({ path: `${SHOTS}/pos-party-size.png` });
  console.log("PASS settings minimums → publish → amount table blocked ($180 short) → per-person table 201 → POS 2 位");
} finally {
  if (orderId) await api("POST", `/online-orders/${orderId}/cancel`);
  await api("PATCH", "/settings", restore);
  await api("POST", "/online-order/publish");
  await browser.close();
}
