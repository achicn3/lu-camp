// 一般商品的編輯與停售瀏覽器煙霧（裁示 2026-09-17）：
// 改品名 → 清單立即更新；停售 → 從清單與 POS 消失、勾「顯示已停售」才看得到、可恢復上架。
// 斷言攔到的 request body 與後端實際回應，而不是只看畫面文字。
//
// 需 backend+frontend 已起；SMOKE_ALLOW_WRITE=1（會建資料並改動，請用隔離測試庫）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立並改動商品，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const run = randomUUID().slice(0, 6);
const sku = `EDIT-${run}`;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

const patches = [];
page.on("request", (req) => {
  if (req.method() === "PATCH" && /\/api\/v1\/catalog-products\//.test(req.url())) {
    try {
      patches.push(JSON.parse(req.postData() ?? "{}"));
    } catch {
      patches.push(null);
    }
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

  const created = await page.request.fetch(`${API}/api/v1/catalog-products`, {
    method: "POST",
    headers: { ...headers, "Idempotency-Key": randomUUID() },
    data: { sku, name: `打錯的品名-${run}`, unit_price: "100", reorder_point: 0 },
  });
  assert.ok(created.ok(), `建商品失敗：${created.status()}`);
  const productId = (await created.json()).id;

  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "一般商品", exact: true }).click();
  const row = page.locator(`tr:has-text("${sku}")`);
  await row.waitFor({ timeout: 10000 });

  // 1) 改品名
  await row.getByRole("button", { name: "編輯" }).click();
  const dialog = page.getByRole("dialog", { name: "編輯商品" });
  await dialog.waitFor();
  await page.screenshot({ path: `${SHOTS}/edit-01-dialog.png` });
  const fixedName = `高山瓦斯罐-${run}`;
  await dialog.getByLabel("品名").fill(fixedName);
  await dialog.getByRole("button", { name: "儲存" }).click();
  await dialog.waitFor({ state: "detached", timeout: 10000 });
  await page.locator(`tr:has-text("${fixedName}")`).waitFor({ timeout: 10000 });
  assert.equal(patches.at(-1)?.name, fixedName, `送出的品名不對：${JSON.stringify(patches.at(-1))}`);
  assert.equal(patches.at(-1)?.sku, undefined, "不該送出 sku（條碼不可改）");
  ok("改品名送出並更新清單", true, fixedName);
  await page.screenshot({ path: `${SHOTS}/edit-02-renamed.png`, fullPage: true });

  // 2) 停售
  const renamedRow = page.locator(`tr:has-text("${fixedName}")`);
  await renamedRow.getByRole("button", { name: "編輯" }).click();
  const stopDialog = page.getByRole("dialog", { name: "編輯商品" });
  await stopDialog.waitFor();
  await page.screenshot({ path: `${SHOTS}/edit-03-stop-confirm.png` });
  await stopDialog.getByRole("button", { name: "停售", exact: true }).click();
  await stopDialog.waitFor({ state: "detached", timeout: 10000 });
  await renamedRow.waitFor({ state: "detached", timeout: 10000 });
  assert.equal(patches.at(-1)?.is_active, false);
  ok("停售後從清單消失", true);

  // POS 掃碼也找不到
  const scanned = await page.request.fetch(`${API}/api/v1/catalog-products/by-sku/${sku}`, {
    headers,
  });
  assert.equal(scanned.status(), 404, `停售商品 POS 仍掃得到：${scanned.status()}`);
  ok("停售後 POS 掃不到", true, "404");

  // 3) 勾「顯示已停售」才看得到，並可恢復上架
  await page.getByLabel("顯示已停售").check();
  const inactiveRow = page.locator(`tr:has-text("${fixedName}")`);
  await inactiveRow.waitFor({ timeout: 10000 });
  assert.ok(
    (await inactiveRow.textContent())?.includes("已停售"),
    "停售中的商品沒有標示",
  );
  await page.screenshot({ path: `${SHOTS}/edit-04-inactive.png`, fullPage: true });
  ok("勾選後看得到並標示已停售", true);

  await inactiveRow.getByRole("button", { name: "編輯" }).click();
  await page.getByRole("button", { name: "恢復上架" }).click();
  await page.waitForFunction(
    (name) => {
      const tr = [...document.querySelectorAll("tr")].find((r) => r.textContent?.includes(name));
      return tr != null && !tr.textContent.includes("已停售");
    },
    fixedName,
    { timeout: 10000 },
  );
  assert.equal(patches.at(-1)?.is_active, true);
  ok("可恢復上架", true);

  // 庫存數量與商品本身都還在（停售只影響找不找得到）
  const after = await (
    await page.request.fetch(`${API}/api/v1/catalog-products/${productId}`, { headers })
  ).json();
  assert.equal(after.sku, sku, "商品被改動或消失了");
  ok("停售/恢復不動商品本身", true, `sku ${after.sku}`);
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
