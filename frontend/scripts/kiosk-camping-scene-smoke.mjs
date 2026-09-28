// 顧客螢幕手繪露營動畫煙霧（店主 2026-09-27）：
//   待機：螢火蟲與營燈故事（畫布真的在動、沒有 JS 例外）
//   結帳：店員推購物車 → 動畫不關、鏡頭帶到營桌（cart），明細寫在紙卡上；再加一件照常顯示
//   取消購物車 → 回待機，且是同一份動畫接著播（沒有重掛）
//   簽署：簽署內容要完整閱讀，動畫藏起來（hidden）；簽完 → 星光連線（celebrate）
//   減少動態效果：畫面停住不播
// 需 backend + frontend 已起、已 seed（dev-manager、dev-kiosk）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/kiosk-camping-scene-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "kiosk-camping");
const RUN = Date.now().toString(36);
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body, extra = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...extra },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

function uniquePhone() {
  return `09${String(Date.now()).slice(-8)}`;
}

function validNationalId() {
  const letters = "ABCDEFGHJKLMNPQRSTUVXYWZIO";
  const code = 10 + Math.floor(Math.random() * 26);
  const letter = letters[code - 10];
  const digits = [1, ...Array.from({ length: 7 }, () => Math.floor(Math.random() * 10))];
  const weights = [8, 7, 6, 5, 4, 3, 2, 1];
  let sum = Math.floor(code / 10) + (code % 10) * 9;
  digits.forEach((d, i) => (sum += d * weights[i]));
  const check = (10 - (sum % 10)) % 10;
  return `${letter}${digits.join("")}${check}`;
}

async function pair(context, mgr) {
  const page = await context.newPage();
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  await page.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-kiosk");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("啟用裝置")');
  await page.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  const code = (await page.textContent(".kiosk-pairing-code"))?.trim();
  const terminal = await api(mgr, "POST", "/api/v1/customer-display/terminals", {
    installation_id: crypto.randomUUID(),
    name: `露營動畫煙霧 ${RUN}`,
  });
  await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminal.json.id}/pair`, { pairing_code: code });
  await page.waitForSelector(".kiosk-standby-title", { timeout: 10000 });
  return { page, terminalId: terminal.json.id };
}

const sceneMode = (page) => page.getAttribute(".camping-scene", "data-mode");
const filmTime = (page) => page.$eval(".camp-film canvas", (el) => Number(el.dataset.storyTime));

async function drawSignature(page) {
  const canvas = page.locator("canvas.kiosk-sign-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("找不到簽名畫布");
  const pts = [[0.15, 0.5], [0.3, 0.25], [0.45, 0.7], [0.6, 0.3], [0.75, 0.6], [0.85, 0.4]];
  await page.mouse.move(box.x + box.width * pts[0][0], box.y + box.height * pts[0][1]);
  await page.mouse.down();
  for (const [fx, fy] of pts.slice(1)) await page.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 12 });
  await page.mouse.up();
}

const pageErrors = [];
const browser = await chromium.launch();
let mgr = null;
let terminalId = null;
let cartRevision = null;
try {
  mgr = (await api(null, "POST", "/api/v1/auth/login", { username: "dev-manager", password: "dev-test-123456" })).json.access_token;
  const context = await browser.newContext({ viewport: { width: 834, height: 1112 } });
  const paired = await pair(context, mgr);
  const { page } = paired;
  terminalId = paired.terminalId;

  // ── 待機 ──
  await page.waitForSelector(".camp-film canvas", { timeout: 10000 });
  ok("待機鋪滿露營動畫", (await sceneMode(page)) === "idle");
  await page.evaluate(() => {
    document.querySelector(".camping-scene").dataset.smokeMarker = "same-node";
  });
  const t1 = await filmTime(page);
  await page.waitForTimeout(1500);
  const t2 = await filmTime(page);
  ok("待機故事持續播放", t2 > t1, `${t1} → ${t2}`);
  await page.screenshot({ path: join(SHOTS, "01-firefly-lantern.png") });

  // ── 結帳 ──
  const current = await api(mgr, "GET", "/api/v1/cash-sessions/current");
  if (current.json === null) await api(mgr, "POST", "/api/v1/cash-sessions/open", { opening_float: "2000" });
  const seller = await api(mgr, "POST", "/api/v1/contacts", {
    name: `動畫煙霧賣方 ${RUN}`,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER"],
  });
  const acq = await api(
    mgr,
    "POST",
    "/api/v1/acquisitions",
    {
      type: "BUYOUT",
      contact_id: seller.json.id,
      payout_method: "CASH",
      items: [
        { name: `手沖壺 ${RUN}`, grade: "A", listed_price: "1200", acquisition_cost: "400" },
        { name: `折疊椅 ${RUN}`, grade: "B", listed_price: "800", acquisition_cost: "250" },
      ],
    },
    { "Idempotency-Key": `camping-${RUN}` },
  );
  const [code1, code2] = acq.json.item_codes;
  const put = async (lines, tenders) => {
    const res = await api(mgr, "PUT", `/api/v1/customer-display/terminals/${terminalId}/cart`, {
      expected_revision: cartRevision,
      lines,
      tenders,
    });
    if (res.status !== 200) throw new Error(`cart: ${res.status}`);
    cartRevision = res.json.revision;
    return res.json;
  };
  await put([{ line_type: "SERIALIZED", item_code: code1 }]);
  await page.locator(".kiosk-cart-item", { hasText: `手沖壺 ${RUN}` }).waitFor({ timeout: 15000 });
  ok("開始結帳保留上方營燈互動", (await sceneMode(page)) === "cart");
  await page.waitForTimeout(1200);
  await page.screenshot({ path: join(SHOTS, "03-cart-transition.png") });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: join(SHOTS, "04-cart.png") });
  const layout = await page.evaluate(() => {
    const header = document.querySelector(".kiosk-cart-header").getBoundingClientRect();
    const items = document.querySelector(".kiosk-cart-items").getBoundingClientRect();
    return { gap: items.top - header.bottom };
  });
  ok("明細上方留一扇窗看得到動畫", layout.gap > 120, `${Math.round(layout.gap)}px`);
  const itemEffect = page.waitForFunction(() => document.querySelector(".camp-film canvas").dataset.effect === "item");
  await put([
    { line_type: "SERIALIZED", item_code: code1 },
    { line_type: "SERIALIZED", item_code: code2 },
  ]);
  await page.locator(".kiosk-cart-item", { hasText: `折疊椅 ${RUN}` }).waitFor({ timeout: 15000 });
  await itemEffect;
  ok("新增商品觸發螢火蟲回饋", true);
  await page.screenshot({ path: join(SHOTS, "05-cart-two-items.png") });
  ok("加第二件保留購物車情境", (await sceneMode(page)) === "cart");

  await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminalId}/cart/cancel`, {
    expected_revision: cartRevision,
    reason: "煙霧測試：取消回待機",
  });
  cartRevision = null;
  await page.waitForSelector(".kiosk-standby-title", { timeout: 15000 });
  await page.waitForTimeout(2500);
  ok("取消購物車回待機", (await sceneMode(page)) === "idle");
  ok(
    "回待機是同一份動畫接著播（沒有重掛）",
    (await page.$eval(".camping-scene", (el) => el.dataset.smokeMarker)) === "same-node",
  );
  const resumed = await filmTime(page);
  await page.waitForTimeout(800);
  ok("返回待機接著播放", await filmTime(page) > resumed);
  await page.screenshot({ path: join(SHOTS, "06-back-to-idle.png") });

  // 真實測試商品現金結帳，付款動畫只由 COMPLETED 狀態觸發。
  const paymentLines = [{ line_type: "SERIALIZED", item_code: code1 }];
  const draft = await put(paymentLines);
  const tenders = [{ tender_type: "CASH", amount: draft.snapshot.total }];
  await put(paymentLines, tenders);
  const checkout = await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminalId}/cart/begin-checkout`, { expected_revision: cartRevision });
  if (checkout.status !== 200) throw new Error(`begin checkout: ${checkout.status}`);
  const sale = await api(mgr, "POST", "/api/v1/sales", {
    lines: [{ line_type: "SERIALIZED", item_code: code1 }],
    tenders,
    cart_session_id: checkout.json.id,
    cart_revision: checkout.json.revision,
  }, { "Idempotency-Key": `film-payment-${RUN}` });
  if (sale.status !== 201) throw new Error(`sale: ${sale.status} ${JSON.stringify(sale.json)}`);
  cartRevision = null;
  await page.waitForFunction(() => document.querySelector(".camp-film canvas").dataset.filmMode === "paid");
  ok("真實付款完成觸發專屬動畫", await sceneMode(page) === "paid");
  await page.waitForTimeout(700);
  await page.screenshot({ path: join(SHOTS, "06b-paid.png") });
  await page.waitForSelector(".kiosk-standby-title", { timeout: 20000 });

  // ── 簽署 ──
  const contact = await api(mgr, "POST", "/api/v1/contacts", {
    name: `動畫簽署客 ${RUN}`,
    phone: uniquePhone(),
    address: "臺北市大安區露營路 88 號",
    national_id: validNationalId(),
    roles: ["SELLER", "MEMBER"],
  });
  const task = await api(mgr, "POST", "/api/v1/signing/tasks", {
    kind: "ACQUISITION_AFFIDAVIT",
    contact_id: contact.json.id,
    terminal_id: terminalId,
    content: { items: [{ name: "登山背包", amount: "1200" }], total: "1200" },
  });
  ok("建立簽署任務", task.status === 201, `status=${task.status}`);
  await page.waitForSelector("button.kiosk-payout-btn", { timeout: 10000 });
  ok("簽署時動畫藏起來、不干擾閱讀", (await sceneMode(page)) === "hidden");
  ok("簽署時動畫不可見", (await page.$eval(".camping-scene", (el) => getComputedStyle(el).visibility)) === "hidden");
  await page.check('.kiosk-agree-check input[type="checkbox"]');
  await page.click('button.kiosk-payout-btn:has-text("現金")');
  await drawSignature(page);
  await page.click("button.kiosk-submit");
  await page.waitForSelector('h1:has-text("已完成簽署")', { timeout: 8000 });
  ok("簽完：星點連成約定", (await sceneMode(page)) === "celebrate");
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(SHOTS, "07-signed-drawing.png") });
  await page.waitForTimeout(2400);
  const tick = await page.$eval(".doodle-tick", (el) => Number(getComputedStyle(el).strokeDashoffset.replace("px", "")));
  ok("手繪勾勾畫完", Math.abs(tick) < 1, `dashoffset=${tick}`);
  ok("簽署完成有獨立於付款的特效", await page.$eval(".camp-film canvas", el => el.dataset.effect === "signed"));
  await page.screenshot({ path: join(SHOTS, "08-signed-celebrate.png") });
  await context.close();

  // ── 減少動態效果 ──
  const calm = await browser.newContext({ viewport: { width: 834, height: 1112 }, reducedMotion: "reduce" });
  const calmPaired = await pair(calm, mgr);
  await calmPaired.page.waitForTimeout(800);
  const c1 = await filmTime(calmPaired.page);
  await calmPaired.page.waitForTimeout(2000);
  const c2 = await filmTime(calmPaired.page);
  ok("減少動態效果：畫面停住不播", c1 === c2 && c1 !== 0, `${c1} / ${c2}`);
  await calmPaired.page.screenshot({ path: join(SHOTS, "09-reduced-motion.png") });
  await calm.close();

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  ok("流程例外", false, String(error));
  const p = browser.contexts().flatMap((c) => c.pages())[0];
  if (p) await p.screenshot({ path: join(SHOTS, "99-failure.png") });
} finally {
  await browser.close();
  if (mgr && terminalId !== null && cartRevision !== null) {
    await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminalId}/cart/cancel`, {
      expected_revision: cartRevision,
      reason: "煙霧測試結束",
    }).catch(() => {});
  }
}
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
if (failures.length > 0) process.exitCode = 1;
