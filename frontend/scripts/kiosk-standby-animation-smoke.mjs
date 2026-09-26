// 顧客螢幕待機畫面店名動畫煙霧（React Bits SplitText）：配對後進待機 → 店名逐字浮現、最後全部看得到、
// 讀出來仍是完整店名；系統「減少動態效果」時不拆字、直接顯示。
// 需 backend + frontend 已起、已 seed（dev-manager、dev-kiosk）。執行：node scripts/kiosk-standby-animation-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "kiosk-standby");
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

async function pairedStandby(context, mgr) {
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
    name: `待機動畫煙霧 ${Date.now()}`,
  });
  await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminal.json.id}/pair`, { pairing_code: code });
  await page.waitForSelector(".kiosk-standby-title", { timeout: 10000 });
  return page;
}

const pageErrors = [];
const browser = await chromium.launch();
try {
  const mgr = (await api(null, "POST", "/api/v1/auth/login", { username: "dev-manager", password: "dev-test-123456" })).json.access_token;

  const context = await browser.newContext({ viewport: { width: 834, height: 1112 } });
  const page = await pairedStandby(context, mgr);
  const title = page.locator(".kiosk-standby-title");
  await page.waitForSelector(".kiosk-standby-title .split-char", { timeout: 5000 });
  await page.waitForTimeout(250);
  await page.screenshot({ path: join(SHOTS, "01-animating.png") });
  const midOpacities = await page.$$eval(".kiosk-standby-title .split-char", (els) =>
    els.map((el) => Number(getComputedStyle(el).opacity)),
  );
  ok("店名拆成逐字、動畫中有字還沒完全出現", midOpacities.length > 1 && midOpacities.some((o) => o < 1), `字數 ${midOpacities.length}`);
  await page.waitForTimeout(3000);
  const endOpacities = await page.$$eval(".kiosk-standby-title .split-char", (els) =>
    els.map((el) => Number(getComputedStyle(el).opacity)),
  );
  ok("動畫結束每個字都看得到", endOpacities.every((o) => o === 1));
  const name = (await title.getAttribute("aria-label")) ?? (await title.innerText());
  ok("讀出來是完整店名（不是一個字一個字）", name.replace(/\s/g, "").length === midOpacities.length, name);
  ok("下方提示照常顯示", (await page.locator(".kiosk-standby-sub").innerText()).length > 0);
  await page.screenshot({ path: join(SHOTS, "02-done.png") });
  await context.close();

  const calm = await browser.newContext({ viewport: { width: 834, height: 1112 }, reducedMotion: "reduce" });
  const calmPage = await pairedStandby(calm, mgr);
  await calmPage.waitForTimeout(500);
  ok(
    "減少動態效果：不拆字、直接顯示店名",
    (await calmPage.locator(".kiosk-standby-title .split-char").count()) === 0 &&
      (await calmPage.locator(".kiosk-standby-title").innerText()).trim().length > 0,
  );
  await calmPage.screenshot({ path: join(SHOTS, "03-reduced-motion.png") });
  await calm.close();

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  console.log(String(error));
  process.exitCode = 1;
} finally {
  await browser.close();
}
