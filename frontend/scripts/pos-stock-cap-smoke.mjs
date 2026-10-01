// POS 重複掃描撞到庫存上限要有提示（店主 2026-10-01）的瀏覽器煙霧。
// 建一件庫存只有 1 的一般商品，在 POS 連掃兩次：數量停在 1，並顯示「庫存只剩 1 件，已加到上限」。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/pos-stock-cap-smoke.mjs
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "pos-stock-cap");
const RUN = Date.now().toString(36).toUpperCase();
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body, extra = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...extra,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: res.status, json: await res.json().catch(() => null) };
}

const token = (
  await api(null, "POST", "/api/v1/auth/login", {
    username: "dev-manager",
    password: "dev-test-123456",
  })
).json.access_token;
if ((await api(token, "GET", "/api/v1/cash-sessions/current")).json === null) {
  await api(token, "POST", "/api/v1/cash-sessions/open", { opening_float: "2000" });
}
const SKU = `CAP-${RUN}`;
const NAME = `最後一罐瓦斯-${RUN}`;
const product = await api(token, "POST", "/api/v1/catalog-products", {
  sku: SKU,
  name: NAME,
  unit_price: "120",
});
const supplier = await api(token, "POST", "/api/v1/suppliers", { name: `上限測試供應商-${RUN}` });
const po = await api(token, "POST", "/api/v1/purchase-orders", {
  supplier_id: supplier.json.id,
  submit: true,
  lines: [{ catalog_product_id: product.json.id, qty: 1, unit_cost: "60" }],
});
await api(
  token,
  "POST",
  `/api/v1/purchase-orders/${po.json.id}/receive`,
  { lines: po.json.lines.map((l) => ({ line_id: l.id, qty: l.qty })) },
  { "Idempotency-Key": `cap-recv-${randomUUID()}` },
);
ok("前置：一般商品入庫 1 件", product.status === 201, `HTTP ${product.status}`);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
await skipOpeningCheckRedirect(page);

async function scan() {
  const box = page.locator('input[name="code"]');
  await page.waitForFunction(() => !document.querySelector('input[name="code"]')?.disabled);
  await box.fill(SKU);
  await box.press("Enter");
}

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });

  await scan();
  await page.getByText(NAME).first().waitFor();
  const notice = page.getByText(`${NAME} 庫存只剩 1 件，已加到上限`);
  ok("第一次掃描：加入、不提示", !(await notice.isVisible()));

  await scan();
  await notice.waitFor({ timeout: 5000 }).catch(() => {});
  ok("第二次掃描：提示庫存只剩 1 件、已加到上限", await notice.isVisible());
  const qty = await page.getByLabel(`${NAME} 數量`).inputValue();
  ok("數量仍是 1（不超賣）", qty === "1", qty);
  await page.screenshot({ path: `${SHOTS}/01-capped.png` });
  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (err) {
  await page.screenshot({ path: `${SHOTS}/error.png` }).catch(() => {});
  ok("煙霧流程", false, String(err));
}

await browser.close();
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
process.exit(failures.length === 0 ? 0 : 1);
