// 真 backend + Postgres：庫存明細「作廢這件」——同一張收購單收兩件，從庫存只作廢其中一件，
// 另一件照常在庫、收購單仍有效、只退回這件的成本。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3005";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8005";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lu-camp-inventory-void-item-shots";
mkdirSync(SHOTS, { recursive: true });
let token;
async function api(path, method = "GET", body) {
  const response = await fetch(`${API}/api/v1${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": randomUUID(),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  assert(response.ok, `${method} ${path}: ${response.status} ${await response.clone().text()}`);
  return response.json();
}

const browser = await chromium.launch();
const errors = [];
try {
  token = (await api("/auth/login", "POST", { username: "dev-manager", password: "dev-test-123456" })).access_token;
  if (!(await api("/cash-sessions/current"))) await api("/cash-sessions/open", "POST", { opening_float: "10000" });
  const seller = await api("/contacts", "POST", {
    name: "庫存作廢測試",
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER", "MEMBER"],
  });
  const created = await api("/acquisitions", "POST", {
    type: "BUYOUT",
    contact_id: seller.id,
    items: [
      { name: "庫存作廢帳篷", grade: "A", acquisition_cost: "1000", listed_price: "1800" },
      { name: "庫存作廢睡袋", grade: "A", acquisition_cost: "800", listed_price: "1200" },
    ],
  });
  const items = await api(`/acquisitions/${created.acquisition_id}/void-items`);
  const tent = items.find((item) => item.name === "庫存作廢帳篷");
  const bag = items.find((item) => item.name === "庫存作廢睡袋");

  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.addInitScript((value) => localStorage.setItem("lu-camp.access-token", value), token);
  await skipOpeningCheckRedirect(page, BASE);
  await page.goto(`${BASE}/inventory`);
  const row = page.getByText(tent.item_code, { exact: true }).locator("xpath=ancestor::tr");
  await row.getByRole("button", { name: "詳細", exact: true }).click();
  const modal = page.getByRole("dialog", { name: "商品明細" });
  await modal.getByText(`這件屬於收購單 #${created.acquisition_id}`).waitFor();
  await page.screenshot({ path: `${SHOTS}/01-detail-with-void.png` });

  await modal.getByRole("button", { name: "作廢這件", exact: true }).click();
  const confirm = page.getByRole("dialog", { name: "作廢收購確認" });
  await confirm.getByText("本次作廢 1 件商品，其餘商品保留。").waitFor();
  await confirm.getByLabel("作廢原因", { exact: true }).fill("賣方反悔，只退帳篷");
  await confirm.getByRole("button", { name: "確認作廢", exact: true }).click();
  await modal.getByText(/已作廢這件/).waitFor();
  await page.screenshot({ path: `${SHOTS}/02-voided.png` });

  const after = await api(`/acquisitions/${created.acquisition_id}/void-items`);
  assert.equal(after.find((item) => item.id === tent.id).voided, true, "帳篷已作廢");
  assert.equal(after.find((item) => item.id === bag.id).status, "IN_STOCK", "睡袋照常在庫");
  assert.equal((await api(`/acquisitions/${created.acquisition_id}`)).voided_at, null, "收購單仍有效");
  assert.match(await modal.textContent(), /退回現金\s*1,000/);
  assert.equal(await modal.getByRole("button", { name: "作廢這件", exact: true }).count(), 0);
  console.log("PASS 庫存明細只作廢這一件、其他商品不受影響、只退這件成本");

  // 同一張收購單的另一件：仍可單獨作廢（不受前一件影響）
  await modal.getByRole("button", { name: "關閉", exact: true }).click();
  const bagRow = page.getByText(bag.item_code, { exact: true }).locator("xpath=ancestor::tr");
  await bagRow.getByRole("button", { name: "詳細", exact: true }).click();
  await page.getByRole("dialog", { name: "商品明細" }).getByRole("button", { name: "作廢這件", exact: true }).waitFor();
  console.log("PASS 同一張收購單的另一件仍可單獨作廢");
  assert.deepEqual(errors, [], `頁面錯誤：${errors.join("\n")}`);
} finally {
  await browser.close();
}
