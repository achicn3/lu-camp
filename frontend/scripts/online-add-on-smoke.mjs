// 加點（店主 2026-10-15）真瀏覽器煙霧：低消桌點不夠 → 出現「我是加點」→ 勾了就能送單（雲端不擋低消）
// → 點夠低消時選項消失、不帶加點 → POS 線上訂單標「桌號・加點」並提醒店員現場確認。
// 結束後取消測試單、還原桌號設定並重新發佈。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1");
const API = "http://localhost:8116/api/v1", POS = "http://localhost:3116", ORDER = "http://localhost:8794";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-add-on-shots";
const TABLE = "煙霧加點";
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
  await api("PATCH", "/settings", {
    dine_in_tables: [...original.dine_in_tables, TABLE],
    dine_in_minimums: { ...restore.dine_in_minimums, [TABLE]: { mode: "amount", amount: "300" } },
  });
  await api("POST", "/online-order/publish");
  const code = (await api("GET", "/online-order/status")).tables.find((t) => t.label === TABLE).code;

  // ① 點一份 $120：還差 $180、送出鈕停用、出現「我是加點」
  const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await guest.goto(`${ORDER}/t/${code}`);
  await guest.getByRole("button", { name: "甜點", exact: true }).click();
  await guest.locator(".item").filter({ hasText: "巴斯克蛋糕" }).locator(".item-add").click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  await guest.getByText("本桌低消 $300，還差 $180。").waitFor();
  const submit = guest.getByRole("button", { name: "送出現金訂單", exact: true });
  const addOn = guest.getByLabel("我是加點（同桌已經點過）");
  assert.ok(await submit.isDisabled());
  assert.ok(await addOn.isVisible());
  assert.ok(await guest.getByText("加點不用達低消，店員會在現場確認。").isHidden());

  // ② 勾加點：缺額訊息收起、可以送出
  await addOn.check();
  await guest.getByText("加點不用達低消，店員會在現場確認。").waitFor();
  assert.ok(await guest.getByText("本桌低消 $300，還差 $180。").isHidden());
  assert.ok(await submit.isEnabled());
  assert.ok(await guest.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await guest.screenshot({ path: `${SHOTS}/guest-add-on-checked.png` });

  // ③ 點夠低消：加點選項消失（已達就不算加點）；減回去：選項回來、仍是勾的
  const plus = guest.locator(".cart-line").first().getByRole("button", { name: "+" });
  await plus.click();
  await guest.locator(".cart-total").filter({ hasText: "$240" }).waitFor();
  await guest.locator(".cart-line").first().getByRole("button", { name: "+" }).click();
  await guest.locator(".cart-total").filter({ hasText: "$360" }).waitFor();
  assert.ok(await addOn.isHidden(), "add-on choice hides once the minimum is met");
  assert.ok(await submit.isEnabled());
  await guest.locator(".cart-line").first().getByRole("button", { name: "−" }).click();
  await guest.locator(".cart-line").first().getByRole("button", { name: "−" }).click();
  await guest.locator(".cart-total").filter({ hasText: "$120" }).waitFor();
  assert.ok(await guest.getByLabel("我是加點（同桌已經點過）").isChecked());

  // ④ 送出：請求帶 add_on、雲端 201
  const resultPromise = guest.waitForResponse(
    (r) => r.url() === `${ORDER}/api/orders` && r.request().method() === "POST", { timeout: 90000 },
  );
  await guest.getByRole("button", { name: "送出現金訂單", exact: true }).click();
  const result = await resultPromise;
  assert.equal(result.request().postDataJSON().add_on, true);
  assert.equal(result.status(), 201);
  await guest.waitForURL(/\/order\//);
  const created = await result.json();
  const publicOrder = await (await fetch(`${ORDER}/api/orders/${created.token}`)).json();

  // ⑤ POS：桌號旁標「加點」、提醒店員現場確認
  let received;
  for (let n = 0; n < 40 && !received; n++) {
    received = (await api("GET", "/online-orders")).orders.find(
      (o) => o.table_label === TABLE && Date.parse(o.created_at) === Date.parse(publicOrder.created_at),
    );
    if (!received) await new Promise((resolve) => setTimeout(resolve, 500));
  }
  assert.ok(received, "add-on order must reach POS");
  orderId = received.id;
  assert.equal(received.add_on, true);
  const pos = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  await skipOpeningCheckRedirect(pos);
  await pos.goto(`${POS}/login`);
  await pos.locator('input[name="username"]').fill("dev-manager");
  await pos.locator('input[name="password"]').fill(PASSWORD);
  await pos.getByRole("button", { name: "登入", exact: true }).click();
  await pos.waitForURL((url) => !url.pathname.startsWith("/login"));
  await pos.goto(`${POS}/pos`);
  await pos.getByRole("button", { name: /線上訂單/ }).click();
  const card = pos.locator(".online-order").filter({ hasText: `桌號 ${TABLE}・加點` }).filter({ hasText: "待付款" });
  await card.getByText("客人選了加點，沒檢查本桌低消，請現場確認是不是同桌加點。").waitFor();
  await card.scrollIntoViewIfNeeded();
  await pos.screenshot({ path: `${SHOTS}/pos-add-on.png` });

  // ⑥ 下一張單不沿用這次的加點：重新選購物車，選項要是沒勾的
  await guest.goto(`${ORDER}/t/${code}`);
  await guest.getByRole("button", { name: "甜點", exact: true }).click();
  await guest.locator(".item").filter({ hasText: "巴斯克蛋糕" }).locator(".item-add").click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  assert.ok(!(await guest.getByLabel("我是加點（同桌已經點過）").isChecked()));
  console.log("PASS short of minimum → add-on choice → checked submits 201 (add_on) → hides at minimum → POS 桌號・加點 → not reused");
} finally {
  if (orderId) await api("POST", `/online-orders/${orderId}/cancel`);
  await api("PATCH", "/settings", restore);
  await api("POST", "/online-order/publish");
  await browser.close();
}
