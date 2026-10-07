// 餐飲選項瀏覽器煙霧（docs/44 §3.2；O2）：
// 菜單頁用表單建兩個選項群組（溫度必選、加購可選最多 2）→ 品項「選項與介紹」掛上兩群組＋填介紹
// → POS 選項彈窗：沒選溫度不能加、停售選項不能點、加價算進單價 → 外帶現金結帳
// → 後端成交品名帶選項、單價＝底價＋加價、選項快照落盤。
//
// 斷言攔到的 request body 與後端實際狀態，不是只看畫面文字。
// 需 backend + frontend 已起，且指向隔離測試庫（SMOKE_ALLOW_WRITE=1）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立品項、選項與銷售，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const run = randomUUID().slice(0, 6);
const latteName = `拿鐵-${run}`;
const tempGroup = `溫度-${run}`;
const extraGroup = `加購-${run}`;
const category = `咖啡-${run}`;
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

const writes = [];
page.on("request", (req) => {
  if (!["POST", "PATCH", "PUT"].includes(req.method())) return;
  if (!/\/api\/v1\/(menu-|sales)/.test(req.url())) return;
  try {
    writes.push({ method: req.method(), url: req.url(), body: JSON.parse(req.postData() ?? "{}") });
  } catch {
    writes.push({ method: req.method(), url: req.url(), body: null });
  }
});

let token = "";
async function api(method, path, body) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
}

async function createGroup(name, min, max, lines) {
  const form = page.getByRole("form", { name: "新增選項群組" });
  await form.getByLabel("群組名稱").fill(name);
  await form.getByLabel("至少選").fill(String(min));
  await form.getByLabel("最多選").fill(String(max));
  await form.getByLabel("選項（一行一個）").fill(lines.join("\n"));
  await form.getByRole("button", { name: "新增群組" }).click();
  await page.getByRole("region", { name }).waitFor();
}

try {
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));

  const cash = await api("GET", "/api/v1/cash-sessions/current");
  if (cash.status !== 200 || cash.body === null) {
    await api("POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  }
  const created = await api("POST", "/api/v1/menu-items", {
    name: latteName,
    unit_price: "150",
    unit_cost: "40",
    category,
  });
  ok("建立品項", created.status === 201, `HTTP ${created.status}`);
  const latteId = created.body.id;

  // 1. 菜單頁：用表單建兩個群組
  await page.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "選項群組", exact: true }).click();
  await createGroup(tempGroup, 1, 1, ["熱", "冰"]);
  await createGroup(extraGroup, 0, 2, ["燕麥奶 +20", "濃縮 +30", "香草 +15"]);
  const groupPosts = writes.filter((w) => w.method === "POST" && w.url.endsWith("/menu-option-groups"));
  ok(
    "新增群組送出加價解析正確",
    JSON.stringify(groupPosts.at(-1)?.body?.options) ===
      JSON.stringify([
        { name: "燕麥奶", price_delta: "20" },
        { name: "濃縮", price_delta: "30" },
        { name: "香草", price_delta: "15" },
      ]),
    JSON.stringify(groupPosts.at(-1)?.body),
  );
  // 香草停售、燕麥奶填成本 8
  const extra = page.getByRole("region", { name: extraGroup });
  await extra.getByLabel("香草 可售").click();
  await extra.getByText("停售").waitFor();
  await extra.getByLabel("燕麥奶 成本").fill("8");
  await extra.getByRole("button", { name: "燕麥奶 儲存" }).click();
  await page.waitForTimeout(500);
  const groups = await api("GET", "/api/v1/menu-option-groups");
  const extraRead = groups.body.find((g) => g.name === extraGroup);
  const byName = Object.fromEntries(extraRead.options.map((o) => [o.name, o]));
  ok(
    "後端：香草停售、燕麥奶成本 8",
    byName["香草"].is_available === false && byName["燕麥奶"].unit_cost === "8",
    JSON.stringify(extraRead.options),
  );
  await extra.screenshot({ path: `${SHOTS}/opt-01-group-card.png` });

  // 2. 品項掛群組＋介紹
  await page.getByRole("tab", { name: "品項", exact: true }).click();
  await page.getByRole("button", { name: `${latteName} 選項與介紹` }).click();
  const dialog = page.getByRole("dialog", { name: `${latteName} 的選項與介紹` });
  await dialog.getByRole("checkbox", { name: new RegExp(tempGroup) }).check();
  await dialog.getByRole("checkbox", { name: new RegExp(extraGroup) }).check();
  await dialog.getByLabel("介紹").fill("濃縮咖啡加鮮奶");
  await dialog.screenshot({ path: `${SHOTS}/opt-02-item-dialog.png` });
  await dialog.getByRole("button", { name: "儲存" }).click();
  await dialog.waitFor({ state: "detached" });
  const item = (await api("GET", "/api/v1/menu-items")).body.find((i) => i.id === latteId);
  ok(
    "後端：品項依序掛上兩群組、介紹已存",
    JSON.stringify(item.option_groups.map((g) => g.name)) === JSON.stringify([tempGroup, extraGroup]) &&
      item.description === "濃縮咖啡加鮮奶",
    JSON.stringify({ groups: item.option_groups.map((g) => g.name), d: item.description }),
  );
  await page.locator(`tr:has-text("${latteName}")`).screenshot({ path: `${SHOTS}/opt-03-menu-row.png` });

  // 3. POS：分類分頁 → 點拿鐵 → 選項彈窗
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector(".pos-menu-tiles");
  const tab = page.getByRole("tab", { name: category });
  if ((await tab.count()) > 0) {
    await tab.click();
    const names = await page.locator(".pos-menu-tile-name").allTextContents();
    ok("分類分頁只剩該分類的品項", names.length === 1 && names[0] === latteName, JSON.stringify(names));
  }
  await page.locator(".pos-menu-tile", { hasText: latteName }).click();
  const optDialog = page.getByRole("dialog", { name: `加入 ${latteName}` });
  await optDialog.waitFor();
  const addBtn = optDialog.getByRole("button", { name: "加入購物車" });
  ok("沒選溫度不能加入", await addBtn.isDisabled());
  ok("停售的香草不能選", await optDialog.getByRole("checkbox", { name: /香草/ }).isDisabled());
  await optDialog.getByRole("radio", { name: /冰/ }).check();
  await optDialog.getByRole("checkbox", { name: /燕麥奶/ }).check();
  await optDialog.getByRole("checkbox", { name: /濃縮/ }).check();
  const priceText = await optDialog.locator(".pos-qty-dialog-price").textContent();
  ok("單價＝150＋20＋30＝200", /200/.test(priceText ?? ""), priceText ?? "");
  await page.screenshot({ path: `${SHOTS}/opt-04-pos-dialog.png` });
  await addBtn.click();
  const lineName = `${latteName}（冰、燕麥奶、濃縮）`;
  await page.getByText(lineName).first().waitFor();

  // 4. 外帶現金結帳
  await page.waitForSelector(".pos-dinein-panel");
  await page.click('.pos-dinein-mode:has-text("外帶")');
  await page.waitForSelector(".pos-checkout:not([disabled])", { timeout: 15000 });
  await page.screenshot({ path: `${SHOTS}/opt-05-pos-cart.png` });
  await page.click(".pos-checkout");
  await page.waitForSelector(".pos-complete", { timeout: 30000 });
  const salePost = writes.find((w) => w.method === "POST" && /\/api\/v1\/sales$/.test(w.url));
  const sentLine = salePost?.body?.lines?.[0];
  const optIds = [byName["燕麥奶"].id, byName["濃縮"].id];
  const coldId = groups.body.find((g) => g.name === tempGroup).options.find((o) => o.name === "冰").id;
  ok(
    "結帳送出 menu_option_ids（排序後）",
    JSON.stringify(sentLine?.menu_option_ids) === JSON.stringify([coldId, ...optIds].sort((a, b) => a - b)),
    JSON.stringify(sentLine),
  );
  const fnb = await api("GET", "/api/v1/sales/fnb");
  const summary = (fnb.body ?? []).find((s) => s.food_items.includes(lineName));
  ok("餐飲交易紀錄列出帶選項的品名", summary !== undefined, `HTTP ${fnb.status}`);
  const detail = summary ? await api("GET", `/api/v1/sales/${summary.id}`) : null;
  const line = detail?.body?.lines?.find((l) => l.description === lineName);
  ok("後端成交單價 200", line !== undefined && Number(line.unit_price) === 200, JSON.stringify(line));
} catch (err) {
  ok("流程執行", false, String(err));
  await page.screenshot({ path: `${SHOTS}/opt-error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
