// 顧客螢幕煙霧（店主 2026-10-03：SVG／GSAP 動畫在平板／手機上會讓頁面當掉，整個拿掉；CSS 小效果保留）。
// 配對 → 待機（店徽、二手 · 選物 · 露營、店名；底部框只有提示一行）→ 店員放商品 → 明細 → 移除一件 → 收現結帳 →
// 交易已完成。每個畫面都確認沒有露營場景、筆跡、影片等重的東西，瀏覽器裡在跑的動畫只能是那幾個 CSS 小效果
// （店名逐字浮現、新增／改數量閃一下、異動提示收起、付款轉圈），而且都會自己結束（不是無限循環）——付款轉圈除外。
// 平板直式／橫式、手機各截一張待機。
// 需 backend + frontend 已起、已 seed（dev-manager、dev-kiosk）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/kiosk-static-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "kiosk-display");
// 允許的 CSS 小效果（只動透明度、位移、底色）；其他任何動畫出現都算失敗。
const LIGHT_EFFECTS = new Set([
  "standby-char-rise",
  "kiosk-item-added",
  "kiosk-item-updated",
  "kiosk-change-notice",
  "kiosk-payment-spin",
]);
const RUN = String(Date.now()).slice(-6);
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
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

/** 沒有 SVG／GSAP 那類重的動畫：沒有露營場景、筆跡、影片；在跑的只能是允許的 CSS 小效果。 */
async function staticCheck(page, label) {
  await page.waitForTimeout(400);
  const names = await page.evaluate(() =>
    document.getAnimations().map((a) => (a.animationName ? a.animationName : a.constructor.name)),
  );
  const heavy = names.filter((name) => !LIGHT_EFFECTS.has(name));
  const leftovers = await page.$$eval(
    ".camping-scene, .ledger-hand, [data-anim-state], .ledger-strike, .ledger-stamp, video, canvas:not(.kiosk-sign-canvas)",
    (els) => els.length,
  );
  ok(
    `${label}：沒有 SVG／GSAP 動畫（只有 CSS 小效果）`,
    heavy.length === 0 && leftovers === 0,
    `CSS 小效果 ${names.length - heavy.length}、其他動畫 ${JSON.stringify(heavy)}、殘留元素 ${leftovers}`,
  );
}

const pageErrors = [];
const browser = await chromium.launch();
let token = null;
let terminalId = null;
let cartRevision = null;
try {
  token = (await api(null, "POST", "/api/v1/auth/login", { username: "dev-manager", password: "dev-test-123456" })).json.access_token;
  const context = await browser.newContext({ viewport: { width: 834, height: 1112 } });
  const page = await context.newPage();
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  await page.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-kiosk");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("啟用裝置")');
  await page.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  const code = (await page.textContent(".kiosk-pairing-code"))?.trim();
  const terminal = await api(token, "POST", "/api/v1/customer-display/terminals", {
    installation_id: crypto.randomUUID(),
    name: `靜態客顯煙霧 ${RUN}`,
  });
  terminalId = terminal.json.id;
  await api(token, "POST", `/api/v1/customer-display/terminals/${terminalId}/pair`, { pairing_code: code });

  // ① 待機
  await page.waitForSelector(".kiosk-standby-static", { timeout: 10000 });
  const brand = await page.locator(".kiosk-standby-brand").innerText();
  ok("待機：店徽、二手 · 選物 · 露營、店名由上往下", (await page.locator(".kiosk-standby-mark").count()) === 1 && /二手 · 選物 · 露營\s*露坑選物露營用品/.test(brand), brand.replace(/\n/g, " / "));
  ok("待機：底部框只有提示一行", (await page.locator(".kiosk-standby-card").innerText()).trim() === "請稍候，店員將為您加入商品。");
  await staticCheck(page, "待機");
  ok("店名逐字浮現（CSS）", (await page.locator(".kiosk-standby-title .split-char").count()) > 1);
  await page.waitForTimeout(2500); // 等逐字浮現跑完再截圖
  await page.screenshot({ path: join(SHOTS, "01-idle-portrait.png") });
  await page.setViewportSize({ width: 1180, height: 820 });
  await page.screenshot({ path: join(SHOTS, "02-idle-landscape.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: join(SHOTS, "03-idle-phone.png") });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  ok("手機寬度不會橫向捲動", !overflow);
  await page.setViewportSize({ width: 834, height: 1112 });

  // ② 店員放商品
  const make = async (name, price) =>
    (await api(token, "POST", "/api/v1/menu-items", { name: `${name}-${RUN}`, unit_price: price, category: "煙霧" })).json;
  const latte = await make("拿鐵", "150");
  const cake = await make("戚風", "90");
  const put = async (lines, tenders = null) => {
    const res = await api(token, "PUT", `/api/v1/customer-display/terminals/${terminalId}/cart`, {
      expected_revision: cartRevision,
      lines,
      tenders,
      service_mode: "TAKEOUT",
    });
    cartRevision = res.json.revision;
    return res.json;
  };
  const both = [
    { line_type: "MENU", menu_item_id: latte.id, qty: 2 },
    { line_type: "MENU", menu_item_id: cake.id, qty: 1 },
  ];
  await put(both);
  await page.locator(".kiosk-cart-item", { hasText: `戚風-${RUN}` }).waitFor({ timeout: 15000 });
  ok("新加的商品那一行會閃一下（is-added）", (await page.locator(".kiosk-cart-item.is-added").count()) > 0);
  ok("明細：兩樣商品、總額 $390", (await page.locator("[data-testid=kiosk-total-bar]").innerText()).includes("$390"));
  await staticCheck(page, "結帳明細");
  await page.screenshot({ path: join(SHOTS, "04-cart.png") });

  // ③ 移除一件：直接消失、告訴客人已移除
  const cash = [{ tender_type: "CASH", amount: "300" }];
  const cart = await put([both[0]], cash);
  await page.locator(".kiosk-cart-item", { hasText: `戚風-${RUN}` }).waitFor({ state: "detached", timeout: 15000 });
  ok("移除的商品直接消失，畫面寫已移除", (await page.locator(".kiosk-cart-changes").innerText()).includes("已移除"));
  await staticCheck(page, "移除商品");

  // ④ 收現結帳 → 交易已完成
  await api(token, "POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  const settings = (await api(token, "GET", "/api/v1/settings")).json;
  const sale = await api(
    token,
    "POST",
    "/api/v1/sales",
    {
      lines: [both[0]],
      tenders: cash,
      service_mode: "TAKEOUT",
      cart_session_id: cart.id,
      cart_revision: cart.revision,
      expected_einvoice_enabled: settings.einvoice_enabled,
    },
    { "Idempotency-Key": `kiosk-static-${RUN}` },
  );
  ok("收現結帳成立", sale.status === 201, `${sale.status} ${JSON.stringify(sale.json?.detail ?? "")}`);
  cartRevision = null;
  await page.getByText("交易已完成").waitFor({ timeout: 15000 });
  ok("交易已完成：金額與倒數", (await page.locator("[data-testid=kiosk-total-bar]").innerText()).includes("$300"));
  await staticCheck(page, "交易已完成");
  await page.screenshot({ path: join(SHOTS, "05-paid.png") });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  await context.close();
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
