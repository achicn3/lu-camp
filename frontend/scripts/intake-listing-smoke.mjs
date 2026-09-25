// 排隊收購 I4 煙霧（docs/42 §7、§8）：付款後的一批（買斷折疊椅 ×2＋散裝營釘 ×10）→ 排隊收購頁進「待整理上架」
// → 清單看到這一批 → 整理上架頁：缺分類標紅、整批套用分類、第一件補品牌 → 營釘先不勾、上架 2 件並印 2 張標籤
// → 部分上架、清單顯示進度 → 營釘改每件售價再上架 → 全部上架；上架後 POS 找得到可賣。
// 需 backend + frontend 已起、已 seed（dev-manager）。執行：node scripts/intake-listing-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "intake-listing");
const RUN = String(Date.now()).slice(-6);
const SELLER = `上架賣家-${RUN}`;
const CATEGORY = `露營椅${RUN}`;
const BRAND = `品牌${RUN}`;
const MODEL = `折疊椅X${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiLogin() {
  const res = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username: "dev-manager", password: "dev-test-123456" }),
  });
  return (await res.json()).access_token;
}

async function api(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

async function pickCombo(scope, label, name, create = false) {
  const input = scope.getByLabel(label, { exact: true });
  await input.fill(name);
  if (create) await scope.getByRole("button", { name: `＋ 建立「${name}」` }).click();
  else await scope.getByRole("option", { name, exact: true }).click();
  await scope.getByTestId("combo-selected").filter({ hasText: name }).first().waitFor();
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
let originalRequire = null;
let mgr = null;

try {
  mgr = await apiLogin();
  originalRequire = (await api(mgr, "GET", "/api/v1/settings")).json.require_acquisition_affidavit;
  await api(mgr, "PATCH", "/api/v1/settings", { require_acquisition_affidavit: false });
  await api(mgr, "POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  await api(mgr, "POST", "/api/v1/categories", { name: CATEGORY });
  const contact = await api(mgr, "POST", "/api/v1/contacts", {
    name: SELLER,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER"],
  });
  const batch = (await api(mgr, "POST", "/api/v1/intake-batches", { contact_id: contact.json.id, declared_item_count: 12 })).json;
  const lineIds = [];
  for (const line of [
    { short_name: "黑色折疊椅", qty: 2, acquisition_type: "BUYOUT", expected_listed_price: "500", deal_cost: "250", grade: "B" },
    { short_name: "營釘", qty: 10, acquisition_type: "BULK_LOT", expected_listed_price: "20", deal_cost: "5" },
  ]) {
    lineIds.push((await api(mgr, "POST", `/api/v1/intake-batches/${batch.id}/lines`, line)).json.id);
  }
  await api(mgr, "POST", `/api/v1/intake-batches/${batch.id}/ready`);
  for (const [index, qty] of [2, 10].entries()) {
    await api(mgr, "PATCH", `/api/v1/intake-batches/${batch.id}/lines/${lineIds[index]}/disposition`, {
      disposition: "ACCEPTED",
      accepted_qty: qty,
    });
  }
  const paid = await api(mgr, "POST", `/api/v1/intake-batches/${batch.id}/pay`, { payout_method: "CASH" });
  ok("準備：付款完成", paid.json?.status === "PAID", JSON.stringify(paid.json?.detail ?? ""));

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  const labels = [];
  await page.route("**/print/label", (route) => {
    labels.push(route.request().postDataJSON());
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
  });

  // 排隊收購 → 待整理上架清單
  await page.goto(`${BASE}/acquisition/intake`, { waitUntil: "networkidle" });
  await page.getByRole("link", { name: "待整理上架" }).click();
  await page.getByRole("heading", { name: "待整理上架" }).waitFor();
  const row = page.locator("tr", { hasText: SELLER });
  await row.waitFor();
  ok("清單列出這一批：今天付款、待整理 12 件", (await row.innerText()).includes("今天") && (await row.innerText()).includes("待整理 12 件"), (await row.innerText()).replace(/\s+/g, " "));
  await page.screenshot({ path: join(SHOTS, "01-awaiting-list.png"), fullPage: true });

  // 整理上架頁
  await row.getByRole("link", { name: "整理上架" }).click();
  await page.getByRole("heading", { name: /整理上架/ }).waitFor();
  const cards = page.locator(".intake-list-item");
  await cards.first().waitFor();
  ok("三張卡片（2 件序號品＋1 堆散裝）、都缺分類", (await cards.count()) === 3 && (await page.getByText("缺分類（上架必填）").count()) === 3);
  ok("成本顯示、不能改", (await cards.first().innerText()).includes("成本 $250"));
  await page.screenshot({ path: join(SHOTS, "02-listing-start.png"), fullPage: true });

  // 整批套用分類 → 第一件建品牌
  const bulk = page.getByLabel("整批套用");
  await pickCombo(bulk, "分類", CATEGORY);
  await bulk.getByRole("button", { name: "套用" }).click();
  ok("整批套用後都有分類", (await page.getByText("缺分類（上架必填）").count()) === 0);
  await pickCombo(cards.first(), "品牌", BRAND, true);
  await pickCombo(cards.first(), "型號", MODEL, true);
  ok(
    "選型號後品名自動變成型號（同收購頁）",
    (await cards.first().locator("summary").innerText()).includes(`品名：${MODEL}`),
  );
  ok("卡片寫「二手商品」不寫序號品", (await page.getByText("二手商品").count()) === 2 && (await page.getByText("序號品").count()) === 0);
  // 營釘先不上
  const pegCard = cards.filter({ hasText: "散裝 ×10" });
  await pegCard.getByRole("checkbox").uncheck();
  await page.screenshot({ path: join(SHOTS, "03-filled.png"), fullPage: true });

  await page.getByRole("button", { name: "上架勾選的 2 件並印標籤" }).click();
  await page.getByText(/已上架 2 件，2 張標籤已送出列印/).waitFor({ timeout: 10000 });
  ok(
    "印 2 張標籤：售價 500、補了品牌型號的那件印品牌、品名＝型號",
    labels.length === 2 &&
      labels.every((l) => l.price === 500) &&
      labels.filter((l) => l.brand === BRAND && l.name === MODEL).length === 1,
    JSON.stringify(labels.map((l) => ({ code: l.code, brand: l.brand, price: l.price }))),
  );
  await page.locator("h2", { hasText: "已上架" }).waitFor();
  ok("剩下營釘一張卡、已上架表列 2 件", (await cards.count()) === 1 && (await page.locator(".intake-lines tbody tr").count()) === 2);
  const partial = await api(mgr, "GET", `/api/v1/intake-batches/${batch.id}`);
  ok("批次變「部分上架」", partial.json.status === "PARTIALLY_LISTED", partial.json.status);
  await page.screenshot({ path: join(SHOTS, "04-partial.png"), fullPage: true });

  // 營釘改每件 25 再上架
  await pegCard.getByLabel(/售價/).fill("25");
  await pegCard.getByRole("checkbox").check();
  await page.getByRole("button", { name: "上架勾選的 1 件並印標籤" }).click();
  await page.getByText("這一批都上架完了。").waitFor({ timeout: 10000 });
  ok("營釘標籤印每件 25", labels.length === 3 && labels[2].price === 25, JSON.stringify(labels[2]));
  const done = await api(mgr, "GET", `/api/v1/intake-batches/${batch.id}`);
  ok("批次變「全部上架」", done.json.status === "LISTED", done.json.status);
  await page.screenshot({ path: join(SHOTS, "05-all-listed.png"), fullPage: true });

  const quote = await api(mgr, "POST", "/api/v1/sales/quote", {
    lines: [{ line_type: "SERIALIZED", item_code: labels[0].code }],
  });
  ok("上架後 POS 可以賣", quote.status === 200, String(quote.status));

  await page.goto(`${BASE}/acquisition/intake/listing`, { waitUntil: "networkidle" });
  ok("全部上架後清單不再列這一批", (await page.locator("tr", { hasText: SELLER }).count()) === 0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/acquisition/intake/${batch.id}/listing`, { waitUntil: "networkidle" });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  ok("手機寬度不會整頁橫向捲動", !overflow);
  await page.screenshot({ path: join(SHOTS, "06-mobile.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  console.log(String(error));
  process.exitCode = 1;
} finally {
  if (mgr !== null && typeof originalRequire === "boolean") {
    await api(mgr, "PATCH", "/api/v1/settings", { require_acquisition_affidavit: originalRequire });
  }
  await browser.close();
}
