// POS 結帳改善煙霧（店主 2026-10-04）：
// ① 顧客螢幕：金額左側顯示總件數（各行數量加總）。
// ② POS：帶備註的商品備註後面接「-條碼末三碼」；應付總額旁顯示總件數；
//    輸入實收現金結帳 → 完成頁列出實收與找零、帶備註商品的品牌／品名／備註（含末三碼）。
// 前置資料走 API（品牌、序號品備註、開帳），畫面操作走瀏覽器。
// 需 backend + frontend 已起、已 seed（dev-manager、dev-kiosk、seed_dev_demo）。流程會賣掉一件序號品與
// 三件一般商品，重跑前庫存不夠就重建 lucamp_e2e 並重新 seed（docs/20 §1-2）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/pos-checkout-notes-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "pos-checkout-notes");
const RUN = String(Date.now()).slice(-6);
const NOTE = `缺營釘一支 ${RUN}`;
const BRAND = `煙霧品牌 ${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body, headers = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

const rows = (json) => (Array.isArray(json) ? json : (json?.items ?? []));
const money = (text) => Number.parseInt(String(text).replace(/[^\d-]/g, ""), 10);

const pageErrors = [];
const browser = await chromium.launch();
let token = null;
let terminalId = null;
let cartRevision = null;
try {
  token = (
    await api(null, "POST", "/api/v1/auth/login", {
      username: "dev-manager",
      password: "dev-test-123456",
    })
  ).json.access_token;

  // ① 顧客螢幕：金額左側的總件數
  const kioskCtx = await browser.newContext({ viewport: { width: 834, height: 1112 } });
  const kiosk = await kioskCtx.newPage();
  kiosk.on("pageerror", (err) => pageErrors.push(`kiosk: ${err}`));
  await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await kiosk.fill('input[name="username"]', "dev-kiosk");
  await kiosk.fill('input[name="password"]', "dev-test-123456");
  await kiosk.click('button:has-text("啟用裝置")');
  await kiosk.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  const pairing = (await kiosk.textContent(".kiosk-pairing-code"))?.trim();
  const terminal = await api(token, "POST", "/api/v1/customer-display/terminals", {
    installation_id: crypto.randomUUID(),
    name: `件數煙霧 ${RUN}`,
  });
  terminalId = terminal.json.id;
  await api(token, "POST", `/api/v1/customer-display/terminals/${terminalId}/pair`, {
    pairing_code: pairing,
  });
  await kiosk.waitForSelector(".kiosk-standby-static", { timeout: 10000 });
  const make = async (name, price) =>
    (
      await api(token, "POST", "/api/v1/menu-items", {
        name: `${name}-${RUN}`,
        unit_price: price,
        category: "煙霧",
      })
    ).json;
  const latte = await make("拿鐵", "150");
  const cake = await make("戚風", "90");
  const put = await api(token, "PUT", `/api/v1/customer-display/terminals/${terminalId}/cart`, {
    expected_revision: cartRevision,
    lines: [
      { line_type: "MENU", menu_item_id: latte.id, qty: 2 },
      { line_type: "MENU", menu_item_id: cake.id, qty: 1 },
    ],
    tenders: null,
    service_mode: "TAKEOUT",
  });
  cartRevision = put.json.revision;
  await kiosk.locator(".kiosk-cart-item", { hasText: `戚風-${RUN}` }).waitFor({ timeout: 15000 });
  const count = kiosk.getByTestId("kiosk-item-count");
  ok("客顯：總件數＝數量加總（拿鐵 2＋戚風 1）", (await count.innerText()).trim() === "共 3 件", await count.innerText());
  const countBox = await count.boundingBox();
  const amountBox = await kiosk.locator(".kiosk-cart-grand-amount strong").boundingBox();
  ok(
    "客顯：件數在金額左側、同一列",
    countBox !== null &&
      amountBox !== null &&
      countBox.x + countBox.width <= amountBox.x &&
      Math.abs(countBox.y + countBox.height - (amountBox.y + amountBox.height)) < 24,
    countBox && amountBox ? `件數右緣 ${Math.round(countBox.x + countBox.width)}、金額左緣 ${Math.round(amountBox.x)}` : "",
  );
  ok("客顯：金額 $390", (await kiosk.locator(".kiosk-cart-grand-amount strong").innerText()).includes("$390"));
  await kiosk.screenshot({ path: join(SHOTS, "01-kiosk-count.png") });
  await kiosk.setViewportSize({ width: 390, height: 844 });
  await kiosk.waitForTimeout(300);
  await kiosk.screenshot({ path: join(SHOTS, "02-kiosk-count-phone.png") });
  const kioskOverflow = await kiosk.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  ok("客顯：手機寬度不會橫向捲動", !kioskOverflow);
  await api(token, "POST", `/api/v1/customer-display/terminals/${terminalId}/cart/cancel`, {
    expected_revision: cartRevision,
    reason: "煙霧測試：客顯件數驗完",
  });
  cartRevision = null;
  await api(token, "POST", `/api/v1/customer-display/terminals/${terminalId}/unpair`).catch(() => {});
  await kioskCtx.close();

  // ② 前置：品牌＋序號品備註、一般商品、開帳
  const brand = await api(token, "POST", "/api/v1/brands", { name: BRAND });
  ok("建立品牌", brand.status === 200, String(brand.status));
  const serialized = rows(
    (await api(token, "GET", "/api/v1/serialized-items?status=IN_STOCK&ownership=OWNED&limit=20")).json,
  ).find((item) => item.ownership_type === "OWNED");
  if (!serialized) throw new Error("沒有在庫的買斷序號品——請重建 lucamp_e2e 並重跑 seed_dev_demo");
  const patched = await api(token, "PATCH", `/api/v1/serialized-items/${serialized.id}`, {
    brand_id: brand.json.id,
    note: NOTE,
  });
  ok("序號品寫入品牌與備註", patched.status === 200, `${patched.status} ${JSON.stringify(patched.json?.detail ?? "")}`);
  const catalog = rows((await api(token, "GET", "/api/v1/catalog-products?limit=50")).json).find(
    (product) => product.quantity_on_hand >= 3 && !(product.note ?? "").trim(),
  );
  if (!catalog) throw new Error("沒有庫存 ≥ 3 且沒備註的一般商品——請重建 lucamp_e2e 並重跑 seed_dev_demo");
  await api(token, "POST", "/api/v1/cash-sessions/open", { opening_float: "3000" });
  const tail = serialized.item_code.slice(-3);

  // ③ POS 畫面
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  page.on("pageerror", (err) => pageErrors.push(`pos: ${err}`));
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  const scanBox = page.locator('input[name="code"]');
  await scanBox.waitFor({ timeout: 15000 });
  const scan = async (code) => {
    await page.waitForFunction(() => !document.querySelector('input[name="code"]')?.disabled);
    await scanBox.fill(code);
    await scanBox.press("Enter");
    await page.waitForTimeout(700);
  };
  await scan(serialized.item_code);
  for (let i = 0; i < 3; i += 1) await scan(catalog.sku);

  const cartNote = page.locator(".pos-line-note");
  await cartNote.first().waitFor({ timeout: 8000 });
  ok(
    "購物車：備註後面接條碼末三碼",
    (await cartNote.first().innerText()).trim() === `備註：${NOTE}-${tail}`,
    await cartNote.first().innerText(),
  );
  ok("購物車：沒備註的商品不加末三碼", (await cartNote.count()) === 1, `備註行 ${await cartNote.count()}`);
  const posCount = page.getByTestId("pos-item-count");
  ok("POS：應付總額旁顯示總件數（序號品 1＋一般商品 3）", (await posCount.innerText()).trim() === "共 4 件", await posCount.innerText());

  await page.waitForFunction(() => !document.querySelector("button.pos-checkout")?.disabled, null, {
    timeout: 15000,
  });
  const total = money(await page.locator(".pos-total strong").innerText());
  const received = Math.ceil((total + 1) / 1000) * 1000;
  await page.getByLabel(/實收現金/).fill(String(received));
  await page.locator(".pos-change").waitFor({ timeout: 5000 });
  await page.screenshot({ path: join(SHOTS, "03-pos-cart.png") });

  await page.locator("button.pos-checkout").click();
  const reminder = page.locator('[aria-label="商品備註提醒"]');
  await reminder.waitFor({ timeout: 8000 });
  ok(
    "結帳提醒：備註也接上末三碼",
    (await reminder.locator(".pos-note-body").first().innerText()).trim() === `${NOTE}-${tail}`,
  );
  await page.screenshot({ path: join(SHOTS, "04-pos-reminder.png") });
  await reminder.locator('button:has-text("已確認，繼續結帳")').click();

  await page.locator(".pos-complete").waitFor({ timeout: 15000 });
  // 列印對話框先關掉，截完成頁全貌
  const skip = page.locator('[role="dialog"] button:has-text("不用"), [role="dialog"] button:has-text("略過"), [role="dialog"] button:has-text("關閉")');
  if (await skip.count()) await skip.first().click().catch(() => {});
  const cash = page.getByRole("group", { name: "現金找零" });
  await cash.waitFor({ timeout: 5000 });
  const cashText = await cash.innerText();
  ok(
    "完成頁：實收現金與找零",
    cashText.includes(`$${received.toLocaleString("en-US")}`) &&
      cashText.includes(`$${(received - total).toLocaleString("en-US")}`),
    cashText.replace(/\n/g, " "),
  );
  const noted = page.getByRole("region", { name: "帶備註的商品" });
  await noted.getByText(BRAND).waitFor({ timeout: 8000 });
  const notedText = await noted.innerText();
  ok(
    "完成頁：帶備註商品列出品牌、品名、備註（含末三碼）",
    notedText.includes(BRAND) && notedText.includes(serialized.name) && notedText.includes(`${NOTE}-${tail}`),
    notedText.replace(/\n/g, " / "),
  );
  ok("完成頁：沒備註的商品不列", (await noted.locator("li").count()) === 1);
  await page.screenshot({ path: join(SHOTS, "05-pos-complete.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  await ctx.close();
} catch (error) {
  ok("流程例外", false, String(error));
  const p = browser.contexts().flatMap((c) => c.pages())[0];
  if (p) await p.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
} finally {
  await browser.close();
  if (token && terminalId !== null && cartRevision !== null) {
    await api(token, "POST", `/api/v1/customer-display/terminals/${terminalId}/cart/cancel`, {
      expected_revision: cartRevision,
      reason: "煙霧測試結束",
    }).catch(() => {});
  }
}
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
if (failures.length > 0) process.exitCode = 1;
