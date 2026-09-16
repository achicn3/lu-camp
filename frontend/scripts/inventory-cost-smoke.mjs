// 真 backend + Postgres：先在專用 E2E DB seed COST-1954(1954/1000)、NO-COST(NULL)，
// 稅率5%、LINE Pay 2.2%、台灣Pay 1%。以兩種角色登入，驗 API、表格及截圖。
// SMOKE_PASSWORD 必填；SMOKE_BASE、SMOKE_API、SMOKE_SHOTS 可覆寫。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const base = process.env.SMOKE_BASE ?? "http://localhost:3104";
const api = process.env.SMOKE_API ?? "http://localhost:8104";
const shots = process.env.SMOKE_SHOTS ?? "/tmp/invcost-shots";
const password = process.env.SMOKE_PASSWORD;
assert.ok(password, "SMOKE_PASSWORD is required");
mkdirSync(shots, { recursive: true });
const browser = await chromium.launch();
let passed = 0;
try {
  for (const role of ["manager", "clerk"]) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${base}/login`);
    await page.locator('input[name="username"]').fill(`invcost-${role}`);
    await page.locator('input[name="password"]').fill(password);
    await page.getByRole("button", { name: "登入", exact: true }).click();
    await page.waitForURL(`${base}/`);
    const token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
    const response = await context.request.get(`${api}/api/v1/catalog-products`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    assert.equal(response.status(), 200);
    const rows = await response.json();
    assert.equal(rows.find((row) => row.sku === "COST-1954").unit_cost, role === "manager" ? "1000" : null);
    assert.equal(rows.find((row) => row.sku === "NO-COST").unit_cost, null);
    passed++;
    console.log(`PASS ${role}: API 成本角色隔離`);
    await page.goto(`${base}/inventory`);
    await page.getByRole("tab", { name: "一般商品", exact: true }).click();
    const costRow = page.getByRole("row").filter({ hasText: "COST-1954" });
    await costRow.waitFor();
    if (role === "manager") {
      await page.getByRole("columnheader", { name: "進貨成本", exact: true }).waitFor();
      await page.getByRole("columnheader", { name: "毛利率", exact: true }).waitFor();
      await costRow.getByRole("cell", { name: "45%", exact: true }).waitFor();
      assert.equal(await costRow.getByRole("cell").nth(4).textContent(), "1,000");
      const noCost = page.getByRole("row").filter({ hasText: "NO-COST" });
      assert.equal(await noCost.getByRole("cell").nth(4).textContent(), "—");
      assert.equal(await noCost.getByRole("cell").nth(5).textContent(), "—");
    } else {
      assert.equal(await page.getByRole("columnheader", { name: "進貨成本", exact: true }).count(), 0);
      assert.equal(await page.getByRole("columnheader", { name: "毛利率", exact: true }).count(), 0);
      assert.equal(await costRow.getByRole("cell").count(), 7);
    }
    assert.deepEqual(errors, []);
    await page.screenshot({ path: `${shots}/${role}.png`, fullPage: true });
    passed++;
    console.log(`PASS ${role}: 庫存欄位、毛利率與截圖`);
    await context.close();
  }
  console.log(`${passed}/4 通過`);
} finally {
  await browser.close();
}
