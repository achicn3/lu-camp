// 線上 LINE Pay 開關（店主 2026-10-20）真瀏覽器煙霧：真 backend＋真 Postgres＋本機 Worker/D1＋真瀏覽器。
// 預設關：客人頁 LINE Pay「即將開放」→ 管理者在「餐飲菜單 → 線上發布」打開 → 客人頁可以選 LINE Pay → 關掉又變回即將開放。
// 需：後端 SMOKE_API（預設 http://localhost:8131/api/v1，已設定連到 SMOKE_ORDER 的 Worker）、POS SMOKE_POS（預設 :3131）、
// Worker SMOKE_ORDER（預設 http://127.0.0.1:8811，要有 LINEPAY_* 測試值）。隔離環境才跑：會新增品項並發佈菜單。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const API = process.env.SMOKE_API ?? "http://localhost:8131/api/v1";
const POS = process.env.SMOKE_POS ?? "http://localhost:3131";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8811";
assert.match(ORDER, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/, "只對本機 Worker 跑");
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-linepay-switch-shots";
mkdirSync(SHOTS, { recursive: true });

let token = "";
async function api(method, path, body) {
  const r = await fetch(API + path, {
    method,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.ok(r.ok, `${method} ${path}: ${r.status} ${await r.clone().text()}`);
  return r.status === 204 ? null : r.json();
}
token = (await api("POST", "/auth/login", { username: "dev-manager", password: PASSWORD })).access_token;
await api("POST", "/menu-items", { name: `煙霧拿鐵${Date.now() % 100000}`, unit_price: "150" });
await api("POST", "/online-order/publish");
await api("PUT", "/online-orders/accepting", { accepting: true });
const linepayStatus = async () => (await (await fetch(`${ORDER}/api/status`)).json()).linepay;
assert.equal(await linepayStatus(), false, "預設關");

const browser = await chromium.launch();
try {
  const pos = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  await skipOpeningCheckRedirect(pos);
  await pos.goto(`${POS}/login`);
  await pos.locator('input[name="username"]').fill("dev-manager");
  await pos.locator('input[name="password"]').fill(PASSWORD);
  await pos.getByRole("button", { name: "登入", exact: true }).click();
  await pos.waitForURL((url) => !url.pathname.startsWith("/login"));
  await pos.goto(`${POS}/menu`, { waitUntil: "networkidle" });
  await pos.getByRole("tab", { name: "線上發布" }).click();
  const box = pos.getByRole("checkbox", { name: "線上點餐開放 LINE Pay" });
  await pos.getByText("目前關閉：客人只能到櫃檯結帳。").waitFor({ timeout: 30000 }); // 等拉單把雲端狀態帶回來
  assert.equal(await box.isChecked(), false);

  let guest;
  // 每次用新的客人分頁（頁面會記得上次停在購物車）
  const guestLinePay = async () => {
    guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await guest.goto(ORDER, { waitUntil: "networkidle" });
    await guest.getByRole("button", { name: "其他", exact: true }).click(); // 沒分類的品項在「其他」
    await guest.locator(".item-add").first().click();
    await guest.getByRole("button", { name: /購物車 \d+ 份/ }).click();
    return guest.getByRole("radio", { name: /LINE Pay/ });
  };
  assert.ok(await (await guestLinePay()).isDisabled(), "關著：客人不能選 LINE Pay");
  await guest.locator(".pay-methods").getByText("即將開放").waitFor();

  await box.click();
  await pos.getByText("客人可以在手機上用 LINE Pay 付款。").waitFor();
  await pos.locator(".online-linepay-switch").screenshot({ path: `${SHOTS}/01-switch-on.png` });
  assert.equal(await linepayStatus(), true, "打開後雲端生效");
  assert.ok(await (await guestLinePay()).isEnabled(), "打開：客人可以選 LINE Pay");
  await guest.screenshot({ path: `${SHOTS}/02-guest-linepay-open.png` });

  await box.click();
  await pos.getByText("目前關閉：客人只能到櫃檯結帳。").waitFor();
  assert.equal(await linepayStatus(), false, "關掉後雲端生效");
  console.log("PASS 預設關 → 打開後客人可選 LINE Pay → 關掉又變回即將開放");
} finally {
  await api("PUT", "/online-orders/linepay", { enabled: false }).catch(() => {});
  await browser.close();
}
