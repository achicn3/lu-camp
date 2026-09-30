// 真 backend + Postgres：全程在 POS UI 解除及重新配對，留存操作截圖。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3005";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8005";
const SHOTS = process.env.SMOKE_SHOTS ?? "deliverables/2026-09-29-pos-fixes";
mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch();
try {
  const login = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "dev-manager", password: "dev-test-123456" }),
  });
  assert(login.ok);
  const { access_token: token } = await login.json();
  const pos = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await pos.addInitScript((value) => localStorage.setItem("lu-camp.access-token", value), token);
  await skipOpeningCheckRedirect(pos);
  await pos.goto(`${BASE}/pos`);
  await pos.getByLabel("顧客螢幕配對碼", { exact: true }).waitFor();

  const kiosk = await browser.newPage({ viewport: { width: 1024, height: 768 } });
  await kiosk.goto(`${BASE}/kiosk`);
  await kiosk.getByLabel("帳號", { exact: true }).fill("dev-kiosk");
  await kiosk.getByLabel("密碼", { exact: true }).fill("dev-test-123456");
  await kiosk.getByLabel("裝置名稱", { exact: true }).fill("收銀台客顯");
  await kiosk.getByRole("button", { name: "啟用裝置", exact: true }).click();
  const code = kiosk.getByLabel("配對碼", { exact: true });
  await code.waitFor();
  await pos.getByLabel("顧客螢幕配對碼", { exact: true }).fill(await code.textContent());
  await pos.getByRole("button", { name: "配對", exact: true }).click();
  await pos.getByRole("button", { name: "解除配對", exact: true }).waitFor();
  await pos.getByText("顧客螢幕已連線", { exact: true }).waitFor({ timeout: 25000 });
  await code.waitFor({ state: "hidden", timeout: 15000 });
  await pos.screenshot({ path: `${SHOTS}/04-pos-paired.png` });

  await pos.getByRole("button", { name: "解除配對", exact: true }).click();
  assert.equal(await pos.getByLabel("解除配對原因", { exact: true }).count(), 0);
  await pos.screenshot({ path: `${SHOTS}/05-pos-confirm-unpair.png` });
  await pos.locator(".pos-kiosk-status").screenshot({ path: `${SHOTS}/05-pos-confirm-unpair-detail.png` });
  await pos.getByRole("button", { name: "確認解除配對", exact: true }).click();
  await code.waitFor({ timeout: 25000 });
  await pos.getByLabel("顧客螢幕配對碼", { exact: true }).fill(await code.textContent());
  await pos.screenshot({ path: `${SHOTS}/06-pos-enter-pairing-code.png` });
  await pos.locator(".pos-kiosk-status").screenshot({ path: `${SHOTS}/06-pos-enter-pairing-code-detail.png` });
  await pos.getByRole("button", { name: "配對", exact: true }).click();
  await pos.getByText("顧客螢幕已連線", { exact: true }).waitFor({ timeout: 25000 });
  await pos.screenshot({ path: `${SHOTS}/07-pos-paired-again.png` });
  await pos.locator(".pos-kiosk-status").screenshot({ path: `${SHOTS}/07-pos-paired-again-detail.png` });
  await code.waitFor({ state: "hidden", timeout: 15000 });
  assert.equal(await code.count(), 0);
  console.log(`PASS POS UI 解除配對 → 輸入新碼 → 配對成功；截圖：${SHOTS}`);
} finally { await browser.close(); }
