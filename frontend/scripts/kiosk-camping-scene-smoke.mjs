// 顧客螢幕手繪露營動畫煙霧（店主 2026-09-27）：
//   待機：露營車無限循環（店主 2026-09-30 裁示），路一直往後跑、沒有 JS 例外
//   結帳：店員推購物車 → 動畫不關、鏡頭帶到營桌（cart），明細寫在紙卡上；再加一件照常顯示
//   取消購物車 → 回待機，且是同一份動畫接著播（沒有重掛）
//   簽署：簽署內容要完整閱讀，動畫藏起來（hidden）；簽完 → 舉杯＋謝謝光臨（celebrate），手繪勾勾畫完
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
const driveTime = (page) => page.$eval(".camping-scene", (el) => Number(el.dataset.time ?? 0));

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
  await page.waitForSelector(".camping-scene #drive", { timeout: 10000 });
  ok("待機鋪滿露營動畫", (await sceneMode(page)) === "idle");
  await page.evaluate(() => {
    document.querySelector(".camping-scene").dataset.smokeMarker = "same-node";
  });
  const t1 = await driveTime(page);
  await page.waitForTimeout(2000);
  const t2 = await driveTime(page);
  ok("待機是露營車一直往前開", t2 > t1 + 1, `${t1} → ${t2}`);
  await page.screenshot({ path: join(SHOTS, "01-drive.png") });

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
  const put = async (lines) => {
    const res = await api(mgr, "PUT", `/api/v1/customer-display/terminals/${terminalId}/cart`, {
      expected_revision: cartRevision,
      lines,
    });
    cartRevision = res.json.revision;
  };
  await put([{ line_type: "SERIALIZED", item_code: code1 }]);
  await page.locator(".kiosk-cart-item", { hasText: `手沖壺 ${RUN}` }).waitFor({ timeout: 15000 });
  ok("開始結帳動畫不關，改成營桌鏡頭", (await sceneMode(page)) === "cart");
  await page.waitForTimeout(1200);
  await page.screenshot({ path: join(SHOTS, "03-cart-transition.png") });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: join(SHOTS, "04-cart.png") });
  const layout = await page.evaluate(() => document.querySelector(".ledger-window").getBoundingClientRect().height);
  ok("手帳上方留一扇窗看得到動畫", layout > 120, `${Math.round(layout)}px`);
  await put([
    { line_type: "SERIALIZED", item_code: code1 },
    { line_type: "SERIALIZED", item_code: code2 },
  ]);
  await page.locator(".kiosk-cart-item", { hasText: `折疊椅 ${RUN}` }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: join(SHOTS, "05-cart-two-items.png") });
  ok("加第二件仍在營桌鏡頭", (await sceneMode(page)) === "cart");

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
  ok("回待機接著開露營車", (await page.locator(".camping-scene #drive").count()) === 1);
  await page.screenshot({ path: join(SHOTS, "06-back-to-idle.png") });

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
  ok("簽完：舉杯慶祝", (await sceneMode(page)) === "celebrate");
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(SHOTS, "07-signed-drawing.png") });
  await page.waitForTimeout(2400);
  const tick = await page.$eval(".doodle-tick", (el) => Number(getComputedStyle(el).strokeDashoffset.replace("px", "")));
  ok("手繪勾勾畫完", Math.abs(tick) < 1, `dashoffset=${tick}`);
  const thanks = await page.$eval(".cs-thanks", (el) => Number(getComputedStyle(el).opacity));
  const word = await page.$eval(".cs-thanks-word", (el) => {
    const r = el.getBoundingClientRect();
    return { top: Math.round(r.top), bottom: Math.round(r.bottom), h: innerHeight };
  });
  ok("天空寫出謝謝光臨（在畫面上半部看得到）", thanks > 0.9 && word.top > 0 && word.bottom < word.h / 2, JSON.stringify(word));
  await page.screenshot({ path: join(SHOTS, "08-signed-celebrate.png") });
  await context.close();

  // ── 減少動態效果 ──
  const calm = await browser.newContext({ viewport: { width: 834, height: 1112 }, reducedMotion: "reduce" });
  const calmPaired = await pair(calm, mgr);
  await calmPaired.page.waitForTimeout(800);
  const c1 = await driveTime(calmPaired.page);
  await calmPaired.page.waitForTimeout(2000);
  const c2 = await driveTime(calmPaired.page);
  ok("減少動態效果：畫面停住不播", c1 === c2, `${c1} / ${c2}`);
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
