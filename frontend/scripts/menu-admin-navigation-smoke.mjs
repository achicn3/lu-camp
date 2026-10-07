// Real backend/Postgres: navigation/filtering must preserve drafts and never write data.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3104";
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/menu-admin-navigation");
mkdirSync(shots, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [], writes = [];
page.on("pageerror", (error) => errors.push(String(error)));
page.on("request", (request) => {
  if (/\/api\/v1\/(menu-|online-order)/.test(request.url()) && ["POST", "PUT", "PATCH", "DELETE"].includes(request.method())) writes.push(request.url());
});
try {
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.getByLabel("帳號", { exact: true }).fill(process.env.SMOKE_USERNAME ?? "dev-manager");
  await page.locator('input[name="password"]').fill(process.env.SMOKE_PASSWORD ?? "dev-test-123456");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  for (const width of [375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
    assert.equal(await page.getByRole("tabpanel").count(), 1);
    assert.equal(await page.getByRole("region", { name: "線上點餐" }).count(), 0);
    assert.equal(await page.getByLabel("品名", { exact: true }).isVisible(), false);
    await page.getByLabel("搜尋品名", { exact: true }).fill("蜜桃");
    await page.getByLabel("販售狀態", { exact: true }).selectOption("available");
    const rows = page.locator("#menu-panel-items tbody tr:visible");
    assert.ok(await rows.count() > 0);
    for (const name of await rows.locator("td:nth-child(2)").allTextContents()) assert.match(name, /蜜桃/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const filterBounds = await page.locator(".menu-items-filters").boundingBox();
    const clearBounds = await page.getByRole("button", { name: "清除篩選", exact: true }).boundingBox();
    assert.ok(clearBounds.x + clearBounds.width <= filterBounds.x + filterBounds.width, "Filter controls must fit their panel");
    await page.screenshot({ path: join(shots, `items-${width}.png`), fullPage: true });
    await page.getByRole("button", { name: "新增品項", exact: true }).click();
    await page.getByLabel("品名", { exact: true }).fill("未儲存的手沖咖啡");
    await page.getByRole("tab", { name: "選項群組", exact: true }).click();
    await page.getByRole("form", { name: "新增選項群組" }).getByLabel("群組名稱").fill("未儲存的溫度");
    await page.getByRole("tab", { name: "分類與排序", exact: true }).click();
    assert.equal(await page.getByRole("tabpanel").count(), 1);
    await page.getByRole("tab", { name: "線上發布", exact: true }).click();
    await page.getByRole("region", { name: "線上點餐" }).waitFor();
    await page.screenshot({ path: join(shots, `publish-${width}.png`), fullPage: true });
    await page.getByRole("tab", { name: "品項", exact: true }).click();
    assert.equal(await page.getByLabel("品名", { exact: true }).inputValue(), "未儲存的手沖咖啡");
    assert.equal(await page.getByLabel("搜尋品名", { exact: true }).inputValue(), "蜜桃");
    await page.getByRole("button", { name: "收起新增品項", exact: true }).click();
    await page.getByRole("button", { name: "新增品項", exact: true }).click();
    assert.equal(await page.getByLabel("品名", { exact: true }).inputValue(), "未儲存的手沖咖啡");
    await page.getByRole("tab", { name: "品項", exact: true }).focus();
    await page.keyboard.press("ArrowRight");
    assert.equal(await page.getByRole("tab", { name: "選項群組", exact: true }).getAttribute("aria-selected"), "true");
    assert.equal(await page.getByRole("form", { name: "新增選項群組" }).getByLabel("群組名稱").inputValue(), "未儲存的溫度");
  }
  assert.deepEqual(writes, []);
  assert.deepEqual(errors, []);
  console.log(`PASS four sections, intersecting filters, draft retention, keyboard and 375/768/1280px. Screenshots: ${shots}`);
} catch (error) {
  await page.screenshot({ path: join(shots, "error.png"), fullPage: true });
  console.error((await page.locator("body").innerText()).slice(0, 4000));
  throw error;
} finally { await browser.close(); }
