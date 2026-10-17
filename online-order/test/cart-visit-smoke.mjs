// 購物車換桌／放太久清空＋品項視窗數量 −／＋（店主 2026-10-17）的真瀏覽器煙霧。
// 只對隔離的本機 wrangler dev（Cloudflare 官方 Turnstile 測試金鑰）跑，絕不指向正式站。
import assert from "node:assert/strict";
import { createHash, createHmac, randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium, devices } from "../../frontend/node_modules/playwright/index.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "Set SMOKE_ALLOW_WRITE=1 for the isolated local Worker");
const base = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8787";
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/, "只對本機 Worker 跑");
const secret = process.env.SMOKE_SECRET;
assert.ok(secret, "Set SMOKE_SECRET to the local Worker's INTEGRATION_SECRET");
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "online-cart-visit");
mkdirSync(shots, { recursive: true });

async function integration(method, path, value) {
  const body = value === undefined ? "" : JSON.stringify(value);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = randomUUID();
  const hash = createHash("sha256").update(body).digest("hex");
  const signature = createHmac("sha256", secret).update([method, path, timestamp, nonce, hash].join("\n")).digest("hex");
  const response = await fetch(`${base}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json", "X-LuCamp-Timestamp": timestamp,
      "X-LuCamp-Nonce": nonce, "X-LuCamp-Signature": signature,
    },
    body: method === "GET" ? undefined : body,
  });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${await response.text()}`);
  return response.json();
}

const run = Date.now();
const tableA = `smokeA${run}`.padEnd(16, "x");
const tableB = `smokeB${run}`.padEnd(16, "x");
const name = `美式 ${run}`;
const limited = `限量 ${run}`;
await integration("PUT", "/integration/menu", {
  version: run, published_at: new Date().toISOString(), store_name: "露坑", font: null,
  categories: [{ id: 1, name: "咖啡" }],
  items: [{
    id: 5, name, description: "煙霧測試品項", category_id: 1, unit_price: 120, photo: null,
    available: true, remaining: null, option_groups: [],
  }, {
    id: 6, name: limited, description: null, category_id: 1, unit_price: 80, photo: null,
    available: true, remaining: 1, option_groups: [],
  }],
});
await integration("PUT", "/integration/tables", {
  revision: run,
  tables: [
    { code: tableA, label: "A1", service_mode: "DINE_IN" },
    { code: tableB, label: "B2", service_mode: "DINE_IN" },
  ],
});
await integration("PUT", "/integration/store-status", { accepting: true });

const browser = await chromium.launch();
const context = await browser.newContext({ ...devices["iPhone 13"] });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
const footer = page.locator(".footer-action");
const qty = page.locator("#detail-qty");
const minus = page.getByRole("button", { name: "少一份" });
const plus = page.getByRole("button", { name: "多一份" });
const feedback = page.locator("#cart-feedback");

async function openItem(itemName = name) {
  await page.getByRole("button", { name: "咖啡", exact: true }).click();
  await page.locator(".item", { hasText: itemName }).locator(".item-detail").click();
  await qty.waitFor();
}
async function ageCart(minutes) {
  await page.evaluate((ms) => {
    const saved = JSON.parse(localStorage.getItem("lk_cart_v2"));
    saved.touched_at = Date.now() - ms;
    localStorage.setItem("lk_cart_v2", JSON.stringify(saved));
  }, minutes * 60_000);
}

try {
  // ① 數量：− 在左、＋ 在右，1 份時 − 不能按，最多 10 份。
  await page.goto(`${base}/t/${tableA}`, { waitUntil: "networkidle" });
  await openItem();
  assert.equal(await qty.inputValue(), "1");
  assert.ok(await minus.isDisabled(), "1 份時 − 不能按");
  const [m, q, p] = await Promise.all([minus.boundingBox(), qty.boundingBox(), plus.boundingBox()]);
  assert.ok(m.x < q.x && q.x < p.x, "− 在輸入框左邊、＋ 在右邊");
  assert.ok(q.height >= 44 && q.width >= 60, `輸入框夠大（${q.width}×${q.height}）`);
  await plus.click(); await plus.click();
  assert.equal(await qty.inputValue(), "3");
  await page.screenshot({ path: join(shots, "1-detail-stepper.png") });
  await qty.fill("99"); await qty.blur();
  assert.equal(await qty.inputValue(), "10", "打超過的數字離開輸入框就拉回上限");
  assert.ok(await plus.isDisabled(), "到上限 ＋ 不能按");
  await minus.click(); await minus.click();
  assert.equal(await qty.inputValue(), "8");
  await qty.fill("2");
  await page.getByRole("button", { name: "加入購物車" }).click();
  await footer.filter({ hasText: "購物車 2 份" }).waitFor();

  // ② 同一桌重新整理：照樣沿用。
  await page.reload({ waitUntil: "networkidle" });
  await footer.filter({ hasText: "購物車 2 份" }).waitFor();
  assert.equal(await feedback.textContent(), "");

  // ③ 掃了另一桌的碼：清空並告知。
  await page.goto(`${base}/t/${tableB}`, { waitUntil: "networkidle" });
  await footer.filter({ hasText: "查看購物車" }).waitFor();
  await feedback.filter({ hasText: "上次沒送出的購物車已經清空" }).waitFor();
  await page.screenshot({ path: join(shots, "2-other-table-cleared.png") });

  // ④ 放著 59 分鐘沒動還在；超過 60 分鐘再打開就清空。
  await openItem();
  await page.getByRole("button", { name: "加入購物車" }).click();
  await footer.filter({ hasText: "購物車 1 份" }).waitFor();
  await ageCart(59);
  await page.reload({ waitUntil: "networkidle" });
  await footer.filter({ hasText: "購物車 1 份" }).waitFor();
  await ageCart(61);
  await page.reload({ waitUntil: "networkidle" });
  await footer.filter({ hasText: "查看購物車" }).waitFor();
  await feedback.filter({ hasText: "已經清空" }).waitFor();

  // ⑤ 分頁留在背景很久再切回來：同樣清空；剛動過的不清。
  await openItem();
  await page.getByRole("button", { name: "加入購物車" }).click();
  await footer.filter({ hasText: "購物車 1 份" }).waitFor();
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await footer.filter({ hasText: "購物車 1 份" }).waitFor();
  await ageCart(61);
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await footer.filter({ hasText: "查看購物車" }).waitFor();

  // ⑥ 視窗開著時補貨：＋ 的上限跟著最新供應（Codex 審查；每 15 秒更新一次）。
  await openItem(limited);
  assert.ok(await plus.isDisabled(), "只剩 1 份時 ＋ 不能按");
  await integration("PUT", "/integration/menu/availability", {
    menu_version: run, revision: run,
    items: [{ id: 5, available: true, remaining: null }, { id: 6, available: true, remaining: 5 }], options: [],
  });
  await page.waitForFunction(() => !document.querySelector('[aria-label="多一份"]')?.disabled, null, { timeout: 25_000 });
  await plus.click();
  assert.equal(await qty.inputValue(), "2");

  assert.deepEqual(errors, []);
  console.log(`cart-visit smoke OK — screenshots in ${shots}`);
} finally {
  await browser.close();
}
