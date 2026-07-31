// 手冊 04：顧客螢幕（手持簽署裝置）啟用與配對。保存兩邊 storageState 供後續腳本重用。
import { join } from "node:path";

import { chromium } from "playwright";

import { BASE, KIOSK, MGR, SHOTS_ROOT, makeShot, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("04-kiosk");
const shot = makeShot(dir);

const browser = await chromium.launch();

// ── 顧客螢幕（直式平板）──
const kioskCtx = await browser.newContext({
  viewport: { width: 834, height: 1112 },
  deviceScaleFactor: 2,
  locale: "zh-TW",
  timezoneId: "Asia/Taipei",
});
const kiosk = await kioskCtx.newPage();
await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
await kiosk.waitForTimeout(600);
await shot(kiosk, "kiosk-login", { locator: ".kiosk-login-card" });

await kiosk.fill('input[name="username"]', KIOSK.u);
await kiosk.fill('input[name="password"]', KIOSK.p);
const deviceNameInput = kiosk.locator('input[name="device_name"], input[name="label"]');
if ((await deviceNameInput.count()) > 0) await deviceNameInput.first().fill("櫃檯顧客螢幕");
await kiosk.click('button:has-text("啟用裝置")');
await kiosk.waitForSelector(".kiosk-pairing-code", { timeout: 15000 });
await kiosk.waitForTimeout(500);
const code = (await kiosk.textContent(".kiosk-pairing-code"))?.trim();
note(`配對碼：${code}`);
await shot(kiosk, "kiosk-pairing-code", { locator: ".kiosk-pairing-card" });

// ── 店員端 POS 配對 ──
const staffCtx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  locale: "zh-TW",
  timezoneId: "Asia/Taipei",
});
const staff = await staffCtx.newPage();
await staff.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await staff.fill('input[name="username"]', MGR.u);
await staff.fill('input[name="password"]', MGR.p);
await staff.click('button:has-text("登入")');
await staff.waitForURL(`${BASE}/`);
await staff.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
await staff.waitForSelector(".pos-kiosk-status", { timeout: 15000 });
await staff.waitForTimeout(800);
await shot(staff, "pos-unpaired", { locator: ".pos-kiosk-status" });

await staff.fill('.pos-kiosk-status input', code);
await staff.click('.pos-kiosk-status button:has-text("配對")');
await staff.waitForSelector(".pos-kiosk-status.is-online", { timeout: 15000 });
await staff.waitForTimeout(600);
await shot(staff, "pos-paired-online", { locator: ".pos-kiosk-status" });

await kiosk.waitForSelector(".kiosk-standby", { timeout: 20000 });
await kiosk.waitForTimeout(800);
await shot(kiosk, "kiosk-standby", { locator: ".kiosk-standby" });

await kioskCtx.storageState({ path: join(SHOTS_ROOT, "kiosk-state.json") });
await staffCtx.storageState({ path: join(SHOTS_ROOT, "staff-state.json") });
note("已保存 kiosk-state.json / staff-state.json");

await browser.close();
console.log("✅ 04-kiosk 完成");
