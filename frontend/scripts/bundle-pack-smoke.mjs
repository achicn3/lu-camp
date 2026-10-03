// 組合包袋裝條碼煙霧（ADR-028；店主 2026-10-04）：
// API 建一個組合價活動（兩種一般商品各 2 件＝4 件，組合價比原價便宜）→ 活動頁「袋裝條碼」填件數建立 →
// 印標籤（攔下送硬體代理的請求，確認條碼／名稱／組合價）→ POS 掃袋裝條碼 → 兩行、共 4 件、應付＝組合價 →
// 現金結帳 → 兩種商品各扣 2 件 → 活動頁停用 → POS 再掃顯示找不到。
// 需 backend + frontend 已起、已 seed（dev-manager、seed_dev_demo）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/bundle-pack-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "bundle-pack");
const RUN = String(Date.now()).slice(-6);
const CAMPAIGN_NAME = `袋裝煙霧 ${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

const rows = (json) => (Array.isArray(json) ? json : (json?.items ?? []));
const money = (text) => Number.parseInt(String(text).replace(/[^\d-]/g, ""), 10);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const token = (
    await api(null, "POST", "/api/v1/auth/login", { username: "dev-manager", password: "dev-test-123456" })
  ).json.access_token;
  const products = rows((await api(token, "GET", "/api/v1/catalog-products?limit=50")).json).filter(
    (p) => p.quantity_on_hand >= 4 && Number(p.unit_price) >= 10 && !(p.note ?? "").trim(),
  );
  if (products.length < 2) throw new Error("需要兩種庫存 ≥ 4 的一般商品——請重建 lucamp_e2e 並重跑 seed_dev_demo");
  const [a, b] = products;
  const listTotal = 2 * Number(a.unit_price) + 2 * Number(b.unit_price);
  // 要比其他活動（seed 有全館九折）更划算才會套用組合價。
  const bundlePrice = Math.floor(listTotal / 2);
  const now = Date.now();
  const target = (p) => ({ target_type: "CATALOG_PRODUCT", target_id: p.id });
  const campaign = await api(token, "POST", "/api/v1/campaigns", {
    name: CAMPAIGN_NAME,
    kind: "BUNDLE",
    bundle_price: String(bundlePrice),
    bundle_slots: [
      { qty: 2, targets: [target(a)] },
      { qty: 2, targets: [target(b)] },
    ],
    starts_at: new Date(now - 3600_000).toISOString(),
    ends_at: new Date(now + 86_400_000).toISOString(),
    applies_owned_serialized: true,
    applies_owned_bulk: true,
    applies_catalog: true,
    applies_consignment: false,
  });
  ok("建組合價活動", campaign.status === 201, `${campaign.status} ${JSON.stringify(campaign.json?.detail ?? "")}`);
  await api(token, "POST", `/api/v1/campaigns/${campaign.json.id}/activate`);
  await api(token, "POST", "/api/v1/cash-sessions/open", { opening_float: "3000" });

  // ① 活動頁建袋
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  const prints = [];
  await page.route("**/print/label", (route) => {
    prints.push(route.request().postDataJSON());
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
  });
  await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });
  const row = page.locator("tr", { hasText: CAMPAIGN_NAME });
  await row.getByRole("button", { name: "袋裝條碼" }).click();
  const panel = page.getByRole("region", { name: `${CAMPAIGN_NAME} 袋裝條碼` });
  await panel.waitFor({ timeout: 8000 });
  const form = panel.getByRole("form", { name: "建立袋裝條碼" });
  await form.getByLabel(`${a.name} 件數`).fill("2");
  await form.getByLabel(`${b.name} 件數`).fill("2");
  ok("面板顯示已放 4／4 件", await form.getByText(/已放 4／4 件/).isVisible());
  await page.screenshot({ path: join(SHOTS, "01-create-pack.png"), fullPage: true });
  await form.getByRole("button", { name: "建立袋裝條碼" }).click();
  const codeCell = panel.locator("td .money").first();
  await codeCell.waitFor({ timeout: 8000 });
  const code = (await codeCell.innerText()).trim();
  ok("建立後列出袋裝條碼 P…", /^P\d+-[0-9A-F]{10}$/.test(code), code);
  await panel.getByRole("button", { name: "印標籤" }).click();
  await panel.getByText("已送出列印").waitFor({ timeout: 5000 });
  ok(
    "印標籤送出條碼、名稱、組合價",
    prints.length === 1 && prints[0].code === code && prints[0].name === CAMPAIGN_NAME && prints[0].price === bundlePrice,
    JSON.stringify(prints),
  );
  await page.screenshot({ path: join(SHOTS, "02-pack-listed.png"), fullPage: true });

  // ② POS 掃袋裝條碼
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  const scanBox = page.locator('input[name="code"]');
  await scanBox.waitFor({ timeout: 15000 });
  await page.waitForFunction(() => !document.querySelector('input[name="code"]')?.disabled);
  await scanBox.fill(code);
  await page.getByText(a.name, { exact: true }).waitFor({ timeout: 8000 });
  ok("POS：袋裡兩樣都加進購物車", await page.getByText(b.name, { exact: true }).isVisible());
  ok("POS：共 4 件", (await page.getByTestId("pos-item-count").innerText()).trim() === "共 4 件");
  await page.waitForFunction(() => !document.querySelector("button.pos-checkout")?.disabled, null, { timeout: 15000 });
  const total = money(await page.locator(".pos-total strong").innerText());
  ok("POS：應付＝組合價", total === bundlePrice, `應付 ${total}、組合價 ${bundlePrice}、原價 ${listTotal}`);
  ok("POS：標出組合價", (await page.locator(".pos-line-bundle").count()) >= 2);
  await page.screenshot({ path: join(SHOTS, "03-pos-pack.png") });
  await page.locator("button.pos-checkout").click();
  await page.locator(".pos-complete").waitFor({ timeout: 15000 });
  ok("結帳完成：總額＝組合價", (await page.locator(".pos-complete").innerText()).includes(`$${bundlePrice.toLocaleString("en-US")}`));
  await page.screenshot({ path: join(SHOTS, "04-pos-complete.png") });
  const after = rows((await api(token, "GET", "/api/v1/catalog-products?limit=50")).json);
  const qtyOf = (p) => after.find((x) => x.id === p.id)?.quantity_on_hand;
  ok(
    "兩種商品各扣 2 件庫存",
    qtyOf(a) === a.quantity_on_hand - 2 && qtyOf(b) === b.quantity_on_hand - 2,
    `${a.quantity_on_hand}→${qtyOf(a)}、${b.quantity_on_hand}→${qtyOf(b)}`,
  );

  // ③ 停用後掃不到
  page.on("dialog", (d) => d.accept());
  await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });
  await page.locator("tr", { hasText: CAMPAIGN_NAME }).getByRole("button", { name: "袋裝條碼" }).click();
  await panel.getByRole("button", { name: "停用" }).click();
  await panel.getByText("已停用").waitFor({ timeout: 8000 });
  ok("活動頁停用袋子", true);
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "開始下一筆" }).click().catch(() => {});
  await scanBox.waitFor({ timeout: 15000 });
  await page.waitForFunction(() => !document.querySelector('input[name="code"]')?.disabled);
  await scanBox.fill(code);
  await page.getByText(/找不到此袋裝條碼/).waitFor({ timeout: 8000 });
  ok("停用後 POS 掃碼顯示找不到", true);
  await page.screenshot({ path: join(SHOTS, "05-pos-deactivated.png") });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  ok("流程例外", false, String(error));
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
if (failures.length > 0) process.exitCode = 1;
