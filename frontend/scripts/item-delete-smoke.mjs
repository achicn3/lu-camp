// 庫存/菜單刪除瀏覽器煙霧：誤建的真的消失、賣過的被擋下並顯示原因（裁示 2026-09-17）。
// 斷言攔到的 request/response，而不是只看畫面文字。
// 需 backend+frontend 已起；SMOKE_ALLOW_WRITE=1（會建資料並刪除，請用隔離測試庫）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立並刪除資料，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const run = randomUUID().slice(0, 6);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));
// 站內確認視窗（不是瀏覽器的 confirm）：若冒出系統對話框代表改壞了，直接讓煙霧失敗。
page.on("dialog", async (d) => {
  ok("不該出現瀏覽器原生對話框", false, d.message());
  await d.dismiss();
});

async function confirmDelete() {
  const dialog = page.getByRole("dialog", { name: "刪除商品" });
  await dialog.waitFor({ timeout: 10000 });
  // 視窗掛在表格儲存格裡，會繼承 `.inv-table td` 的 nowrap，整段說明曾經衝出白框。
  // 量實際寬度而不是看截圖：日後又被哪條 nowrap 規則吃到，這裡會紅。
  const box = await page.evaluate(() => {
    const card = document.querySelector(".confirm-dialog");
    return { scrollW: card.scrollWidth, clientW: card.clientWidth };
  });
  assert.ok(
    box.scrollW <= box.clientW + 1,
    `確認視窗文字超出框外：scrollWidth ${box.scrollW} > clientWidth ${box.clientW}`,
  );
  await dialog.getByRole("button", { name: "刪除", exact: true }).click();
}

const deletes = [];
page.on("response", async (res) => {
  if (res.request().method() === "DELETE" && /\/api\/v1\//.test(res.url())) {
    deletes.push({ url: res.url(), status: res.status() });
  }
});

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', process.env.SMOKE_PASSWORD ?? "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  const token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  ok("登入成功", true);

  async function apiPost(path, data) {
    const res = await page.request.fetch(`${API}/api/v1${path}`, {
      method: "POST",
      headers: { ...headers, "Idempotency-Key": randomUUID() },
      data,
    });
    assert.ok(res.ok(), `${path}: ${res.status()} ${await res.text()}`);
    return res.json();
  }

  // 1) 誤建的一般商品 → 真的刪掉
  const sku = `DEL-${run}`;
  await apiPost("/catalog-products", { sku, name: `誤建商品-${run}`, unit_price: "100", reorder_point: 0 });
  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "一般商品", exact: true }).click();
  await page.getByPlaceholder("搜尋").first().fill(run).catch(() => {});
  const row = page.locator(`tr:has-text("${sku}")`);
  await row.waitFor({ timeout: 10000 });
  await page.screenshot({ path: `${SHOTS}/del-01-before.png`, fullPage: true });
  await row.locator('button:has-text("刪除")').click();
  await page.screenshot({ path: `${SHOTS}/del-01b-confirm.png` });
  await confirmDelete();
  await row.waitFor({ state: "detached", timeout: 10000 });
  const deleted = deletes.find((d) => d.status === 204);
  assert.ok(deleted, `沒有成功的刪除請求：${JSON.stringify(deletes)}`);
  ok("誤建的一般商品真的被刪除", true, `204 ${new URL(deleted.url).pathname}`);
  await page.screenshot({ path: `${SHOTS}/del-02-after.png`, fullPage: true });

  // 該商品確實從後端消失
  const listed = await (
    await page.request.fetch(`${API}/api/v1/catalog-products?q=${run}`, { headers })
  ).json();
  assert.equal(listed.filter((p) => p.sku === sku).length, 0, "後端仍查得到已刪商品");
  ok("後端查不到已刪商品", true);

  // 2) 賣過的商品 → 擋下並顯示原因
  const soldSku = `SOLD-${run}`;
  const soldProduct = await apiPost("/catalog-products", {
    sku: soldSku,
    name: `賣過商品-${run}`,
    unit_price: "100",
    reorder_point: 0,
  });
  await apiPost("/purchase-orders", {
    supplier_id: (await apiPost("/suppliers", { name: `供應商-${run}` })).id,
    submit: true,
    lines: [{ catalog_product_id: soldProduct.id, qty: 1, unit_cost: "50" }],
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "一般商品", exact: true }).click();
  const soldRow = page.locator(`tr:has-text("${soldSku}")`);
  await soldRow.waitFor({ timeout: 10000 });
  await soldRow.locator('button:has-text("刪除")').click();
  await confirmDelete();
  await soldRow.locator('[role="alert"]').waitFor({ timeout: 10000 });
  const blocked = deletes.find((d) => d.status === 409);
  assert.ok(blocked, `沒有被擋下的刪除請求：${JSON.stringify(deletes)}`);
  const reason = await soldRow.locator('[role="alert"]').textContent();
  assert.ok(reason?.includes("採購"), `擋下的原因沒顯示給店員：${reason}`);
  ok("有紀錄的商品被擋下並說明原因", true, reason.trim());
  await page.screenshot({ path: `${SHOTS}/del-03-blocked.png`, fullPage: true });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
