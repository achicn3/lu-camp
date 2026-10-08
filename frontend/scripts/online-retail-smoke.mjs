// 帶回家零售商品煙霧（docs/63 §13、M1d）：真 backend／Postgres ＋ 本機 Worker/D1 ＋ 真後台、客人頁、POS。
// 後台從現有商品挑上線（搜尋、介紹、加購角色、上傳照片）→ 發佈 → 客人首頁「帶回家」區加入 → 購物車提醒到櫃檯領取
// → 送現金單 → POS 拉單保留現量 → 帶入結帳成一般商品行 → 收現 → 清單「已付款・待交貨」→ 客人頁提醒領取
// → 按「已交貨」→ 客人頁「已領取」；庫存只扣一次。375px 不橫向捲動。
// 只准對隔離測試環境執行（SMOKE_ALLOW_WRITE=1）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, devices } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const BASE = process.env.SMOKE_BASE ?? "http://localhost:3500";
const API = process.env.SMOKE_API ?? "http://localhost:8114";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8799";
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/online-retail");
const PHOTO = join(dirname(fileURLToPath(import.meta.url)), "../../online-order/public/brew/citrus.jpg");
mkdirSync(SHOTS, { recursive: true });

let token = "";
async function api(method, path, body) {
  const response = await fetch(`${API}/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}
async function must(method, path, body) {
  const result = await api(method, path, body);
  assert.ok(result.status < 300, `${method} ${path}: ${result.status} ${JSON.stringify(result.body)}`);
  return result.body;
}
async function waitFor(fn, label, ms = 30000) {
  const deadline = Date.now() + ms;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`等不到：${label}`);
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}
const results = [];
const ok = (name, pass, detail = "") => {
  results.push(pass);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
};
const onHand = async (id) => (await must("GET", `/catalog-products/${id}`)).quantity_on_hand;

token = (await must("POST", "/auth/login", { username: USERNAME, password: PASSWORD })).access_token;
const run = randomUUID().slice(0, 6);
const name = `耶加雪菲-${run}`;
const product = await must("POST", "/catalog-products", { name, unit_price: "450", sku: `BEAN-${run}` });
const stocktake = await must("POST", "/stocktakes");
await must("POST", `/stocktakes/${stocktake.id}/confirm`, { counts: [{ catalog_product_id: product.id, counted_qty: 3 }] });
await api("POST", "/cash-sessions/open", { opening_float: "1000" });
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

  // ① 後台：挑商品上線
  await desk.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  await desk.getByRole("tab", { name: "線上發布" }).click();
  await desk.getByRole("link", { name: "新增帶回家商品" }).click();
  await desk.waitForURL(/\/menu\/retail\/new$/);
  const form = desk.getByRole("form", { name: "帶回家商品" });
  await form.getByLabel("搜尋商品").fill(name);
  await form.getByRole("radio", { name: new RegExp(name) }).check();
  await form.getByLabel("介紹").fill("柑橘、茉莉花香，淺焙");
  await form.getByLabel("加購角色").selectOption("bean");
  await desk.screenshot({ path: join(SHOTS, "01-admin-new.png"), fullPage: true });
  await form.getByRole("button", { name: "儲存" }).click();
  await desk.waitForURL(/\/menu\/retail\/\d+$/);
  ok("存好轉到編輯頁（可以加照片）", true);
  await desk.getByLabel(`${name} 上傳照片`).setInputFiles(PHOTO);
  await desk.locator("img.menu-photo-thumb").waitFor({ timeout: 20000 });
  ok("照片上傳成功", true);
  await desk.screenshot({ path: join(SHOTS, "02-admin-edit.png"), fullPage: true });
  await desk.getByRole("link", { name: "← 回線上發布" }).click();
  const listed = desk.locator("li", { hasText: name });
  await listed.waitFor();
  const rowText = await listed.innerText();
  ok("列表內容正確", ["$450", "庫存 3", "加購：咖啡豆", "有照片"].every((t) => rowText.includes(t)), rowText.replace(/\n/g, " "));

  await must("POST", "/online-order/publish");
  const code = (await must("GET", "/online-order/status")).tables.find((t) => t.label === "A1")?.code;
  await must("PUT", "/online-orders/accepting", { accepting: true });

  // ② 客人：帶回家區 → 加入 → 送單
  const guestCtx = await browser.newContext({ ...devices["iPhone 13"] });
  const guest = await guestCtx.newPage();
  guest.on("pageerror", (e) => errors.push(`客人頁：${e}`));
  await guest.goto(`${ORDER}/t/${code}`, { waitUntil: "networkidle" });
  const section = guest.locator("#take-home");
  await section.waitFor();
  const card = section.locator("article", { hasText: name });
  ok("首頁有「帶回家」區、依分類分組、有照片與介紹",
    (await card.count()) === 1 && (await card.locator("img").count()) === 1 && (await card.innerText()).includes("柑橘"));
  ok("首頁入口有「帶回家」", (await guest.getByRole("button", { name: "帶回家", exact: true }).count()) === 1);
  ok("剩 3 件有標出來", (await card.innerText()).includes("剩 3 件"));
  await section.screenshot({ path: join(SHOTS, "03-guest-take-home.png") });
  await card.getByRole("button", { name: `加入：${name}` }).click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  const cartText = await guest.locator("#cart-body").innerText();
  ok("購物車提醒到櫃檯領取", cartText.includes("帶回家商品請到櫃檯領取"), cartText.replace(/\n/g, " "));
  await guest.screenshot({ path: join(SHOTS, "04-guest-cart.png"), fullPage: true });
  const overflow = await guest.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  ok("手機不橫向捲動", overflow <= 0, `溢出 ${overflow}px`);
  const note = `帶回家-${run}`;
  await guest.locator("#order-note").fill(note);
  await guest.getByRole("button", { name: "送出現金訂單" }).click();
  await guest.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 45000 });

  // ③ POS 拉單：保留現量
  const order = await waitFor(async () => (await must("GET", "/online-orders")).orders.find((o) => o.note === note), "POS 拉到單");
  await waitFor(async () => (await must("GET", "/online-orders")).orders.find((o) => o.id === order.id && o.hold_status === "HELD"), "保留成功");
  ok("拉單即保留：庫存 3 → 2", (await onHand(product.id)) === 2);
  await guest.getByText("請到櫃台付現金").waitFor({ timeout: 20000 });
  ok("客人訂單頁也提醒到櫃檯領取", (await guest.locator("#order-body").innerText()).includes("帶回家商品請到櫃檯領取"));

  // ④ POS 帶入結帳、收現
  await desk.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await desk.getByRole("button", { name: /線上訂單/ }).click();
  const sheet = desk.getByRole("dialog", { name: "線上訂單" });
  const row = sheet.getByRole("listitem").filter({ hasText: note });
  ok("清單標出帶回家商品", (await row.innerText()).includes(`${name} ×1（帶回家）`));
  await row.getByRole("button", { name: "帶入結帳" }).click();
  await sheet.waitFor({ state: "detached" });
  ok("帶入購物車：一般商品行", (await desk.getByLabel(`${name} 數量`).count()) === 1);
  await desk.waitForSelector(".pos-checkout:not([disabled])", { timeout: 15000 });
  await desk.click(".pos-checkout");
  await desk.waitForSelector(".pos-complete", { timeout: 30000 });
  ok("結帳成立，庫存只扣一次（仍是 2）", (await onHand(product.id)) === 2);

  // ⑤ 待交貨 → 已交貨
  await desk.getByRole("button", { name: "不用，完成" }).click().catch(() => {});
  await desk.getByRole("button", { name: "開始下一筆" }).click();
  await desk.getByRole("button", { name: /線上訂單/ }).click();
  const paidRow = desk.getByRole("dialog", { name: "線上訂單" }).getByRole("listitem").filter({ hasText: note });
  await paidRow.getByText("已付款・待交貨").waitFor({ timeout: 15000 });
  ok("付了錢還留在清單：已付款・待交貨", true);
  await desk.screenshot({ path: join(SHOTS, "05-pos-awaiting.png") });
  await guest.getByText("已付款。帶回家商品請到櫃檯領取。").waitFor({ timeout: 30000 });
  ok("客人頁：已付款、請到櫃檯領取", true);
  await paidRow.getByRole("button", { name: "已交貨" }).click();
  await paidRow.getByText("已交貨").first().waitFor({ timeout: 15000 });
  await guest.getByText("已領取，謝謝你。").waitFor({ timeout: 30000 });
  ok("交貨後客人頁：已領取", true);
  await guest.screenshot({ path: join(SHOTS, "06-guest-picked-up.png"), fullPage: true });
  ok("頁面無 JS 例外", errors.length === 0, errors.join(" | "));
} catch (error) {
  ok("流程例外", false, String(error));
  await desk.screenshot({ path: join(SHOTS, "zz-error.png"), fullPage: true }).catch(() => {});
} finally {
  // 下線這次的帶回家商品：留著會讓其他煙霧（體驗卡加購）多推一包豆子
  const mine = (await api("GET", "/online-order/retail")).body?.filter((row) => row.product_name === name) ?? [];
  for (const row of mine) await api("DELETE", `/online-order/retail/${row.id}`);
  await browser.close();
}
const failed = results.filter((pass) => !pass).length;
console.log(`\n${results.length - failed}/${results.length} PASS；截圖 ${SHOTS}`);
process.exit(failed ? 1 : 0);
