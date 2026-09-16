// 真 backend + Postgres，僅限隔離測試環境。自行透過 API 建商品、供應商、採購單並收貨。
// SMOKE_ALLOW_WRITE=1、SMOKE_PASSWORD 必填；管理者/店員預設 dev-manager/dev-clerk，可用
// SMOKE_USERNAME_MANAGER/CLERK 覆寫。預期毛利由 API 設定獨立計算，不引用產品計算函式。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const base = process.env.SMOKE_BASE ?? "http://localhost:3000";
const api = process.env.SMOKE_API ?? "http://localhost:8000";
const shots = process.env.SMOKE_SHOTS ?? "/tmp/inventory-cost-shots";
const password = process.env.SMOKE_PASSWORD;
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "Use an isolated test DB and opt in with SMOKE_ALLOW_WRITE=1");
assert.ok(password, "SMOKE_PASSWORD is required");
mkdirSync(shots, { recursive: true });
const browser = await chromium.launch();
const run = randomUUID().slice(0, 8);
const costSku = `COST-${run}`;
const noCostSku = `NONE-${run}`;
let expectedMargin;
let passed = 0;
try {
  for (const role of ["manager", "clerk"]) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${base}/login`);
    await page.locator('input[name="username"]').fill(process.env[`SMOKE_USERNAME_${role.toUpperCase()}`] ?? `dev-${role}`);
    await page.locator('input[name="password"]').fill(password);
    await page.getByRole("button", { name: "登入", exact: true }).click();
    await page.waitForURL(`${base}/`);
    const token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
    const headers = { Authorization: `Bearer ${token}` };
    async function request(path, method = "GET", data) {
      const response = await context.request.fetch(`${api}/api/v1${path}`, {
        method, headers: { ...headers, "Idempotency-Key": randomUUID() }, data,
      });
      assert.ok(response.ok(), `${method} ${path}: ${response.status()}`);
      return response.json();
    }
    if (role === "manager") {
      const settings = await request("/settings");
      const values = [settings.tax_rate, settings.linepay_fee_pct, settings.taiwanpay_fee_pct];
      assert.ok(values.every((v) => v != null && String(v).trim() !== "" && Number.isFinite(Number(v))));
      const [tax, linepay, taiwanpay] = values.map(Number);
      const received = Math.round(1954 / (1 + tax)) - Math.round(1954 * Math.max(linepay, taiwanpay));
      expectedMargin = `${Math.round((received - 1000) * 100 / received)}%`;
      const product = await request("/catalog-products", "POST", { sku: costSku, name: costSku, unit_price: "1954", reorder_point: 0 });
      await request("/catalog-products", "POST", { sku: noCostSku, name: noCostSku, unit_price: "100", reorder_point: 0 });
      const supplier = await request("/suppliers", "POST", { name: `成本測試供應商-${run}` });
      const po = await request("/purchase-orders", "POST", { supplier_id: supplier.id, submit: true, lines: [{ catalog_product_id: product.id, qty: 1, unit_cost: "1000" }] });
      await request(`/purchase-orders/${po.id}/receive`, "POST", { lines: [{ line_id: po.lines[0].id, qty: 1 }] });
    }
    const rows = await request(`/catalog-products?q=${run}`);
    assert.equal(rows.find((row) => row.sku === costSku).unit_cost, role === "manager" ? "1000" : null);
    assert.equal(rows.find((row) => row.sku === noCostSku).unit_cost, null);
    passed++;
    await page.goto(`${base}/inventory`);
    await page.getByRole("tab", { name: "一般商品", exact: true }).click();
    await page.getByPlaceholder("品名 / 商品編號").fill(run);
    await page.getByRole("button", { name: "查詢", exact: true }).click();
    const costRow = page.getByRole("row").filter({ has: page.getByRole("cell", { name: costSku, exact: true }).first() });
    await costRow.waitFor();
    if (role === "manager") {
      await page.getByRole("columnheader", { name: "最新進價", exact: true }).waitFor();
      await page.getByRole("columnheader", { name: "預估毛利率", exact: true }).waitFor();
      await costRow.getByRole("cell", { name: expectedMargin, exact: true }).waitFor();
      assert.equal(await costRow.getByRole("cell").nth(4).textContent(), "1,000");
      const noCost = page.getByRole("row").filter({ has: page.getByRole("cell", { name: noCostSku, exact: true }).first() });
      assert.equal(await noCost.getByRole("cell").nth(4).textContent(), "—");
      assert.equal(await noCost.getByRole("cell").nth(5).textContent(), "—");
    } else {
      assert.equal(await page.getByRole("columnheader", { name: "最新進價", exact: true }).count(), 0);
      assert.equal(await page.getByRole("columnheader", { name: "預估毛利率", exact: true }).count(), 0);
      assert.equal(await costRow.getByRole("cell").count(), 7);
    }
    assert.deepEqual(errors, []);
    await page.screenshot({ path: `${shots}/inventory-cost-${role}.png`, fullPage: true });
    passed++;
    console.log(`PASS ${role}: API 成本角色遮罩、畫面與獨立毛利預期值`);
    await context.close();
  }
  console.log(`${passed}/4 通過`);
} finally {
  await browser.close();
}
