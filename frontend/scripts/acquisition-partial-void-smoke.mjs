// 真 backend + Postgres：選品作廢、客顯解除後自動取碼並重新配對。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3005";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8005";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-partial-void-shots";
mkdirSync(SHOTS, { recursive: true });
let token;
async function api(path, method = "GET", body) {
  const response = await fetch(`${API}/api/v1${path}`, { method, headers: {
    "Content-Type": "application/json", "Idempotency-Key": randomUUID(),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }, body: body ? JSON.stringify(body) : undefined });
  assert(response.ok, `${method} ${path}: ${response.status} ${await response.clone().text()}`);
  return response.json();
}
const browser = await chromium.launch();
const errors = [];
try {
  token = (await api("/auth/login", "POST", { username: "dev-manager", password: "dev-test-123456" })).access_token;
  if (!(await api("/cash-sessions/current"))) await api("/cash-sessions/open", "POST", { opening_float: "10000" });
  const member = await api("/contacts", "POST", { name: "選品作廢測試", phone: uniquePhone(), national_id: validNationalId(), roles: ["SELLER", "MEMBER"] });
  const created = await api("/acquisitions", "POST", { type: "BUYOUT", contact_id: member.id, items: [
    { name: "測試帳篷", grade: "A", acquisition_cost: "1000", listed_price: "1800" },
    { name: "測試睡袋", grade: "A", acquisition_cost: "800", listed_price: "1200" },
  ] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.addInitScript((value) => localStorage.setItem("lu-camp.access-token", value), token);
  await skipOpeningCheckRedirect(page, BASE);
  await page.goto(`${BASE}/acquisition/records`);
  await page.getByText(`#${created.acquisition_id}`, { exact: true }).locator("..").getByRole("button", { name: "選品作廢", exact: true }).click();
  await page.getByRole("checkbox", { name: /測試帳篷/ }).check();
  await page.getByRole("button", { name: "作廢收購", exact: true }).click();
  await page.getByText("本次作廢 1 件商品，其餘商品保留。").waitFor();
  await page.getByLabel("作廢原因", { exact: true }).fill("只退帳篷");
  await page.getByRole("button", { name: "確認作廢", exact: true }).click();
  await page.getByText(/已作廢所選商品/).waitFor();
  let items = await api(`/acquisitions/${created.acquisition_id}/void-items`);
  assert.equal(items.find((item) => item.name === "測試帳篷").voided, true);
  assert.equal(items.find((item) => item.name === "測試睡袋").status, "IN_STOCK");
  assert.equal((await api(`/acquisitions/${created.acquisition_id}`)).voided_at, null);
  await page.locator(".acq-void-section").screenshot({ path: `${SHOTS}/01-partial-void.png` });
  await page.getByRole("checkbox", { name: /測試睡袋/ }).check();
  await page.getByRole("button", { name: "作廢收購", exact: true }).click();
  await page.getByLabel("作廢原因", { exact: true }).fill("退回剩餘睡袋");
  await page.getByRole("button", { name: "確認作廢", exact: true }).click();
  await page.getByText(`已作廢收購單 #${created.acquisition_id}。`, { exact: true }).waitFor();
  assert.notEqual((await api(`/acquisitions/${created.acquisition_id}`)).voided_at, null);
  console.log("PASS 選品作廢、剩餘商品保留、最後全單作廢");

  const context = await browser.newContext({ viewport: { width: 1024, height: 768 } });
  const kiosk = await context.newPage();
  kiosk.on("pageerror", (error) => errors.push(String(error)));
  await kiosk.goto(`${BASE}/kiosk`);
  await kiosk.getByLabel("帳號", { exact: true }).fill("dev-kiosk");
  await kiosk.getByLabel("密碼", { exact: true }).fill("dev-test-123456");
  await kiosk.getByRole("button", { name: "啟用裝置", exact: true }).click();
  const code = kiosk.getByLabel("配對碼", { exact: true });
  await code.waitFor();
  const terminal = await api("/customer-display/terminals", "POST", { installation_id: randomUUID(), name: "自動重連測試櫃檯" });
  await api(`/customer-display/terminals/${terminal.id}/pair`, "POST", { pairing_code: await code.textContent() });
  await kiosk.getByText("櫃檯 · 自動重連測試櫃檯").waitFor({ timeout: 20000 });
  assert.equal(await kiosk.getByRole("button", { name: /解除配對|重新產生配對碼/ }).count(), 0);
  await context.setOffline(true);
  await kiosk.waitForTimeout(16000); // 跨一個 heartbeat / 裝置狀態輪詢週期。
  await context.setOffline(false);
  await kiosk.getByText("櫃檯 · 自動重連測試櫃檯").waitFor();
  assert.equal(await kiosk.getByLabel("帳號", { exact: true }).count(), 0);
  await api(`/customer-display/terminals/${terminal.id}/unpair`, "POST", { reason: "重新配對測試" });
  await code.waitFor({ timeout: 25000 });
  assert.match(await code.textContent(), /^\d{6}$/);
  assert.equal(await kiosk.getByRole("button", { name: /取得配對碼|重新產生配對碼/ }).count(), 0);
  await kiosk.screenshot({ path: `${SHOTS}/02-auto-pairing-code.png` });
  await api(`/customer-display/terminals/${terminal.id}/pair`, "POST", { pairing_code: await code.textContent() });
  await kiosk.getByText("櫃檯 · 自動重連測試櫃檯").waitFor({ timeout: 20000 });
  await kiosk.screenshot({ path: `${SHOTS}/03-paired-again.png` });
  assert.deepEqual(errors, []);
  console.log("PASS 客顯短暫離線保留配對、解除後自動取碼、再次配對");
  console.log(`Screenshots: ${SHOTS}`);
} finally { await browser.close(); }
