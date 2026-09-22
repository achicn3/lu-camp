// 對隔離的真後端／Postgres：折數鑑價 → 收購 → 種類搜尋 → 編輯 → 重新讀回。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { uniquePhone, validNationalId } from "./_national-id.mjs";

const base = process.env.SMOKE_BASE ?? "http://localhost:3800";
const apiBase = process.env.SMOKE_API_BASE ?? "http://localhost:8800";
const shots = process.env.SMOKE_SHOTS ?? "/home/test/tmp/lu-camp-shots/acquisition-discount";
const run = Date.now();
const directPrice = process.env.SMOKE_DIRECT_PRICE === "1";
mkdirSync(shots, { recursive: true });
let token;
async function api(path, method = "GET", body) {
  const response = await fetch(`${apiBase}/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert(response.ok, `${method} ${path}: ${response.status} ${await response.clone().text()}`);
  return response.json();
}
token = (await api("/auth/login", "POST", { username: "dev-manager", password: process.env.SMOKE_PASSWORD ?? "dev-test-123456" })).access_token;
await api("/settings", "PATCH", { default_margin_pct: 45, tax_rate: "0.0500", linepay_fee_pct: "0.0220", taiwanpay_fee_pct: "0.0100", require_acquisition_affidavit: false });
const brand = await api("/brands", "POST", { name: `折數品牌${run}` });
const model = await api("/product-models", "POST", { name: `測試型號${run}`, brand_id: brand.id });
const category = await api("/categories", "POST", { name: `折數種類${run}`, target_margin_pct: 45 });
const current = await fetch(`${apiBase}/api/v1/cash-sessions/current`, { headers: { Authorization: `Bearer ${token}` } });
if (!current.ok || !(await current.json())) await api("/cash-sessions/open", "POST", { opening_float: "10000" });
const opening = await api("/opening-check/today");
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  await page.addInitScript(({ token, day }) => {
    localStorage.setItem("lu-camp.access-token", token);
    localStorage.setItem(`lu-camp.opening-check.${day}`, "1");
  }, { token, day: opening.business_date });
  await page.goto(`${base}/acquisition`);
  await page.getByRole("heading", { name: "收購鑑價入庫" }).waitFor();
  await page.getByRole("button", { name: /建立新賣方/ }).click();
  await page.getByLabel("姓名", { exact: true }).fill(`折數賣家${run}`);
  await page.getByLabel("手機", { exact: true }).fill(uniquePhone(run));
  await page.getByLabel("身分證字號", { exact: true }).fill(validNationalId(run));
  await page.getByRole("button", { name: "建立並選取" }).click();
  for (const [label, value] of [["品牌", brand.name], ["型號", model.name], ["分類", category.name]]) {
    await page.getByLabel(label, { exact: true }).fill(value);
    await page.getByRole("option", { name: value, exact: true }).click();
  }
  assert.equal(await page.getByLabel("品名", { exact: true }).inputValue(), model.name);
  const listed = page.getByLabel("上架售價（含稅與手續費）", { exact: true });
  if (directPrice) {
    await page.getByLabel("成色", { exact: true }).selectOption("A");
    await listed.fill("500");
    assert.equal(await page.getByLabel("參考價（原價或目前最低價）").inputValue(), "");
  } else {
    await page.getByLabel("參考價（原價或目前最低價）").fill("1000");
    await page.getByRole("button", { name: "5折", exact: true }).click();
  }
  await page.waitForFunction(() => document.querySelector('input[aria-label="收購價"]')?.value === "256");
  assert.equal(await listed.inputValue(), "500");
  assert.equal(await page.getByLabel("成色", { exact: true }).inputValue(), "A");
  await page.screenshot({ path: join(shots, "01-discount-pricing.png"), fullPage: true });
  await page.getByText("查看未稅價、手續費與實得", { exact: true }).click();
  assert.deepEqual(await page.locator(".acq-price-breakdown dd").allTextContents(), ["476 元", "11 元", "465 元"]);
  await page.screenshot({ path: join(shots, "05-price-breakdown.png"), fullPage: true });
  await page.getByLabel("收購價", { exact: true }).fill("250");
  await listed.fill("499");
  await page.getByRole("tab", { name: "散裝", exact: true }).click();
  await page.getByRole("tab", { name: "買斷", exact: true }).click();
  assert.equal(await listed.inputValue(), "499");
  assert.equal(await page.getByLabel("收購價", { exact: true }).inputValue(), "250");
  await page.getByRole("button", { name: "送出收購", exact: true }).click();
  await page.getByText(/收購完成/).first().waitFor();
  const items = await api(`/serialized-items?q=${encodeURIComponent(category.name)}`);
  assert.equal(items.length, 1);
  assert.equal(items[0].resale_discount_pct, directPrice ? null : 50);
  if (directPrice) assert.equal(items[0].retail_price, null);
  assert.equal(items[0].listed_price, "499");
  await page.goto(`${base}/inventory`);
  await page.getByLabel("搜尋", { exact: true }).fill(category.name);
  await page.getByLabel("搜尋", { exact: true }).press("Enter");
  const row = page.getByRole("row").filter({ hasText: model.name });
  await row.getByRole("button", { name: "編輯", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "編輯商品" });
  for (const label of ["品牌", "型號", "種類", "成色"]) {
    const style = await dialog.getByLabel(label, { exact: true }).evaluate((element) => {
      const select = getComputedStyle(element);
      const input = getComputedStyle(element.closest("form").querySelector('input[aria-label="品名"]'));
      return { height: element.getBoundingClientRect().height, radius: select.borderRadius, expectedRadius: input.borderRadius,
        background: select.backgroundColor, expectedBackground: input.backgroundColor, font: select.fontSize, expectedFont: input.fontSize };
    });
    assert(style.height >= 44, `${label} should have a touch-friendly height`);
    assert.equal(style.radius, style.expectedRadius);
    assert.equal(style.background, style.expectedBackground);
    assert.equal(style.font, style.expectedFont);
  }
  await dialog.getByLabel("成色", { exact: true }).selectOption("B");
  await dialog.getByLabel("可售折數").fill("4");
  await dialog.getByLabel("商品備註").fill("缺營釘一支");
  await dialog.getByLabel("售價", { exact: true }).fill("400");
  await page.screenshot({ path: join(shots, "02-edit-details.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await dialog.getByLabel("成色", { exact: true }).focus();
  assert(await dialog.getByLabel("成色", { exact: true }).evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return bounds.left >= 0 && bounds.right <= window.innerWidth && style.outlineWidth === "2px";
  }));
  await page.screenshot({ path: join(shots, "06-mobile-edit-details.png"), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await dialog.getByRole("button", { name: "儲存", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  const updated = (await api(`/serialized-items?q=${encodeURIComponent(category.name)}`))[0];
  assert.equal(updated.grade, "B");
  assert.equal(updated.resale_discount_pct, 40);
  assert.equal(updated.note, "缺營釘一支");
  assert.equal(updated.listed_price, "400");
  await page.screenshot({ path: join(shots, "03-search-by-category.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${base}/acquisition`);
  await page.getByLabel("參考價（原價或目前最低價）").fill("1000");
  await page.getByRole("button", { name: "自訂", exact: true }).click();
  await page.getByLabel("自訂折數", { exact: true }).fill("6.5");
  assert.equal(await listed.inputValue(), "650");
  await page.getByRole("tab", { name: "散裝", exact: true }).click();
  await page.getByRole("tab", { name: "買斷", exact: true }).click();
  assert.equal(await page.getByLabel("自訂折數", { exact: true }).inputValue(), "6.5");
  await page.screenshot({ path: join(shots, "04-mobile-custom-discount.png"), fullPage: true });
  assert.deepEqual(errors, []);
  console.log(`PASS: ${directPrice ? "無參考價直接定價" : "折數定價"}、型號帶品名、手動覆寫、收購保存、種類搜尋、原子編輯、手機自訂折數`);
  console.log(`Screenshots: ${shots}`);
} catch (error) {
  await page.screenshot({ path: join(shots, "failure.png"), fullPage: true });
  throw error;
} finally {
  await browser.close();
}
