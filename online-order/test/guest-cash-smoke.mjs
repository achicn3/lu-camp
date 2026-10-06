// Real local Worker + D1 guest checkout smoke. Run against an isolated wrangler dev with
// Cloudflare's official test Turnstile sitekey/secret. Never point this at production.
import assert from "node:assert/strict";
import { createHash, createHmac, randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium, devices } from "../../frontend/node_modules/playwright/index.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "Set SMOKE_ALLOW_WRITE=1 for the isolated local Worker");
const base = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8787";
const secret = process.env.SMOKE_SECRET;
assert.ok(secret, "Set SMOKE_SECRET to the local Worker's INTEGRATION_SECRET");
const shots = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "online-cash");
mkdirSync(shots, { recursive: true });

async function integration(method, path, value) {
  const body = value === undefined ? "" : JSON.stringify(value);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = randomUUID();
  const hash = createHash("sha256").update(body).digest("hex");
  const canonical = [method, path, timestamp, nonce, hash].join("\n");
  const signature = createHmac("sha256", secret).update(canonical).digest("hex");
  const response = await fetch(`${base}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-LuCamp-Timestamp": timestamp,
      "X-LuCamp-Nonce": nonce,
      "X-LuCamp-Signature": signature,
    },
    body: method === "GET" ? undefined : body,
  });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${await response.text()}`);
  return response.json();
}

const status = await (await fetch(`${base}/api/status`)).json();
assert.equal(status.turnstile_site_key, "1x00000000000000000000AA", "Use Cloudflare's test sitekey locally");
const run = Date.now();
await integration("PUT", "/integration/menu", {
  version: run,
  published_at: new Date().toISOString(),
  store_name: "露坑", font: null,
  categories: [{ id: 1, name: "咖啡" }],
  items: [{
    id: 5, name: `拿鐵 ${run}`, description: "煙霧測試品項", category_id: 1,
    unit_price: 150, photo: null, available: true, remaining: null,
    option_groups: [{ id: 8, name: "溫度", min_select: 1, max_select: 1,
      options: [{ id: 9, name: "冰", price_delta: 10, available: true, remaining: null }] }],
  }],
});
await integration("PUT", "/integration/store-status", { accepting: true });
await integration("GET", "/integration/orders");

const browser = await chromium.launch();
const context = await browser.newContext({ ...devices["iPhone 13"] });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
let firstToken = "";
try {
  await page.goto(base, { waitUntil: "networkidle" });
  await page.locator(".item", { hasText: `拿鐵 ${run}` }).click();
  await page.getByRole("button", { name: "加入購物車" }).click();
  await page.getByText("請依每組規則選好選項。").waitFor();
  await page.getByLabel("冰").check();
  await page.locator(".qty-input").fill("2");
  await page.getByRole("button", { name: "加入購物車" }).click();
  await page.getByRole("button", { name: /購物車 2 份/ }).click();
  await page.getByText("合計 $320").waitFor();
  await page.screenshot({ path: join(shots, "cart.png"), fullPage: true });
  await page.getByRole("button", { name: "−" }).click();
  await page.getByText("合計 $160").waitFor();
  await page.locator("#order-note").fill("少冰");

  await page.route("**/api/orders", async (route) => {
    const response = await route.fetch();
    assert.ok(response.ok(), `create order: ${response.status()} ${await response.text()}`);
    firstToken = (await response.json()).token;
    await route.abort("failed"); // Simulate a lost response after Worker committed the order.
    await page.unroute("**/api/orders");
  });
  await page.getByRole("button", { name: "送出現金訂單" }).click();
  await page.getByText(/尚未確認訂單是否成立/).waitFor({ timeout: 30000 });
  await page.reload();
  await page.getByRole("button", { name: "重試確認訂單" }).waitFor();
  await page.getByRole("button", { name: "重試確認訂單" }).click();
  await page.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 30000 });
  assert.equal(page.url().split("/").at(-1), firstToken, "Retry must return the same order token");
  await page.getByText("合計 $160").waitFor();
  await page.screenshot({ path: join(shots, "order.png"), fullPage: true });
  await page.reload();
  await page.getByText("合計 $160").waitFor();
  assert.deepEqual(errors, [], "No browser JavaScript errors");
  console.log(`Guest cash smoke passed. Screenshots: ${shots}`);
} catch (error) {
  await page.screenshot({ path: join(shots, "error.png"), fullPage: true }).catch(() => {});
  console.error("Page state:", await page.locator("body").innerText().catch(() => "unavailable"));
  console.error("Browser errors:", errors);
  console.error("Challenge state:", await page.evaluate(() => ({
    loaded: Boolean(window.turnstile), script: document.querySelector("script[data-turnstile]")?.outerHTML,
    widget: document.querySelector("#challenge-widget")?.innerHTML,
  })).catch(() => "unavailable"));
  throw error;
} finally {
  await browser.close();
}
