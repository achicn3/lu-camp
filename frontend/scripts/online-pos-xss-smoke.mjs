// 上線前資安（docs/44 §8.4）真瀏覽器煙霧：惡意字串在客人頁與 POS 都只能當文字顯示、不會執行。
// ① 店家菜單品名夾帶 HTML／script → 客人菜單、購物車、訂單頁、POS 線上訂單、帶入結帳後的 POS 購物車
// ② 雲端 D1 被竄改（桌號、品名塞惡意字串）→ POS 線上訂單清單
// 需要本機示範環境（後端 :8116、POS :3116、Worker :8794）與 wrangler（直接改本機 D1 模擬竄改）。
// 結束後取消測試單、刪掉測試品項並重新發佈。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1");
const API = "http://localhost:8116/api/v1", POS = "http://localhost:3116", ORDER = "http://localhost:8794";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-pos-xss-shots";
const D1 = process.env.SMOKE_D1_ARGS; // 例："o4-local-demo --local --config <wrangler.json> --persist-to <state>"
const WRANGLER = process.env.SMOKE_WRANGLER ?? "../online-order/node_modules/.bin/wrangler";
assert.ok(D1, "需要 SMOKE_D1_ARGS 指到本機示範 D1");
mkdirSync(SHOTS, { recursive: true });

const run = randomUUID().slice(0, 8);
const XSS = `<img src=x onerror="window.__xss='name'"><script>window.__xss='script'</script>${run}`;
const TAMPER = `<svg onload="window.__xss='tamper'"></svg>${run}`;

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
const sql = (q) => execFileSync(WRANGLER, ["d1", "execute", ...D1.split(" "), "--command", q], { stdio: "pipe" });
/** 頁面上沒有被注入的元素、也沒有執行過任何注入的程式。 */
async function clean(page, where) {
  assert.equal(await page.evaluate(() => window.__xss), undefined, `${where}: injected script ran`);
  assert.equal(await page.locator("img[onerror], svg[onload]").count(), 0, `${where}: injected element`);
  assert.equal(await page.evaluate(() => [...document.scripts].filter((s) => s.text.includes("__xss")).length), 0, `${where}: injected script`);
}

token = (await api("POST", "/auth/login", { username: "dev-manager", password: PASSWORD })).access_token;
const item = await api("POST", "/menu-items", { name: XSS, unit_price: "50", category: "咖啡" });
const orderIds = [];
const browser = await chromium.launch();
try {
  await api("POST", "/online-order/publish");

  // ① 客人：菜單、購物車、送單後的訂單頁
  const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await guest.goto(ORDER);
  await guest.getByRole("button", { name: "咖啡", exact: true }).click();
  const card = guest.locator(".item").filter({ hasText: run });
  await card.getByText(XSS).waitFor();
  await clean(guest, "guest menu");
  await card.locator(".item-add").click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  await guest.locator(".cart-line").getByText(XSS).waitFor();
  await clean(guest, "guest cart");
  await guest.getByRole("button", { name: "送出現金訂單", exact: true }).click();
  await guest.waitForURL(/\/order\//, { timeout: 90000 });
  await guest.locator("#order-body").getByText(XSS, { exact: false }).waitFor();
  await clean(guest, "guest order page");
  await guest.screenshot({ path: `${SHOTS}/01-guest-order.png` });

  // ① POS：線上訂單清單 → 帶入結帳 → POS 購物車
  let imported;
  for (let n = 0; n < 40 && !imported; n++) {
    imported = (await api("GET", "/online-orders")).orders.find((o) => o.lines.some((l) => l.name.includes(run)));
    if (!imported) await new Promise((r) => setTimeout(r, 500));
  }
  assert.ok(imported, "guest order reached POS");
  orderIds.push(imported.id);
  const pos = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  await skipOpeningCheckRedirect(pos);
  await pos.goto(`${POS}/login`);
  await pos.locator('input[name="username"]').fill("dev-manager");
  await pos.locator('input[name="password"]').fill(PASSWORD);
  await pos.getByRole("button", { name: "登入", exact: true }).click();
  await pos.waitForURL((url) => !url.pathname.startsWith("/login"));
  await pos.goto(`${POS}/pos`);
  await pos.getByRole("button", { name: /線上訂單/ }).click();
  const posCard = pos.locator(".online-order").filter({ hasText: run });
  await posCard.getByText(XSS, { exact: false }).waitFor();
  await clean(pos, "POS online list");
  await posCard.getByRole("button", { name: "帶入結帳" }).click();
  await pos.getByRole("button", { name: "取消帶入" }).waitFor();
  await pos.getByText(XSS, { exact: false }).filter({ visible: true }).first().waitFor();
  await clean(pos, "POS cart after load");
  await pos.screenshot({ path: `${SHOTS}/02-pos-cart.png` });
  await pos.getByRole("button", { name: "取消帶入" }).click();

  // ② 竄改雲端 D1：複製這張單成一張新單，桌號與品名換成惡意字串，讓 POS 拉進來
  const fake = randomUUID().replaceAll("-", "");
  const q = (v) => `'${String(v).replaceAll("'", "''")}'`;
  const cols = (table) => JSON.parse(execFileSync(WRANGLER, [
    "d1", "execute", ...D1.split(" "), "--json", "--command", `SELECT name FROM pragma_table_info('${table}')`,
  ], { stdio: "pipe" }).toString())[0].results.map((c) => c.name);
  const copy = (table, keep, set) => {
    const names = cols(table);
    const values = names.map((n) => (n in set ? q(set[n]) : n));
    return `INSERT INTO ${table} (${names.join(", ")}) SELECT ${values.join(", ")} FROM ${table} WHERE ${keep};`;
  };
  sql(
    copy("orders", `id = ${q(imported.remote_id)}`, {
      id: fake, idem_key: fake, token_hash: fake, fingerprint: fake,
      service_mode: "DINE_IN", table_label: TAMPER, sync_status: "NEW",
    }) +
    copy("order_lines", `order_id = ${q(imported.remote_id)}`, { order_id: fake, name: TAMPER }),
  );
  let tampered;
  for (let n = 0; n < 60 && !tampered; n++) {
    tampered = (await api("GET", "/online-orders")).orders.find((o) => o.remote_id === fake);
    if (!tampered) await new Promise((r) => setTimeout(r, 500));
  }
  assert.ok(tampered, "tampered order was imported by POS");
  orderIds.push(tampered.id);
  assert.equal(tampered.table_label, TAMPER.slice(0, 20), "table label is stored as plain text (trimmed to 20)");
  await pos.getByRole("button", { name: /線上訂單/ }).click();
  const tamperedCard = pos.locator(".online-order").filter({ hasText: TAMPER.slice(0, 20) });
  await tamperedCard.getByText(TAMPER, { exact: false }).waitFor();
  await clean(pos, "POS list with tampered cloud data");
  await tamperedCard.scrollIntoViewIfNeeded();
  await pos.screenshot({ path: `${SHOTS}/03-pos-tampered.png` });
  console.log("PASS menu-name XSS inert on guest menu/cart/order + POS list/cart; tampered D1 table/name inert on POS");
} finally {
  for (const id of orderIds) await api("POST", `/online-orders/${id}/cancel`).catch(() => {});
  await api("DELETE", `/menu-items/${item.id}/delete`).catch(() => api("DELETE", `/menu-items/${item.id}`));
  await api("POST", "/online-order/publish");
  await browser.close();
}
