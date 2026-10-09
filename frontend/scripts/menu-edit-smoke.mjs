// 餐飲品項重新設定內容（店主 2026-10-09）瀏覽器 E2E：
// 菜單頁每列「編輯」→ 同一個視窗改品名、分類、售價、成本、介紹 → 儲存 → 清單與後端都更新，
// POS 餐飲磚顯示新品名與新價；成本清空＝未知（送 null）；售價 0 擋下不送。
// 需 backend + frontend 已起、已 seed（dev-manager）。會建立品項，請指向隔離測試庫：
//   SMOKE_ALLOW_WRITE=1 node scripts/menu-edit-smoke.mjs
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "menu-edit");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立品項，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const run = randomUUID().slice(0, 6);
const oldName = `拿鐵-${run}`;
const newName = `燕麥拿鐵-${run}`;
const newCategory = `特調-${run}`;
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

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
page.on("pageerror", (err) => ok("頁面沒有 JS 錯誤", false, String(err)));
const patches = [];
page.on("request", (req) => {
  if (req.method() === "PATCH" && /\/api\/v1\/menu-items\/\d+$/.test(req.url())) {
    patches.push(JSON.parse(req.postData() ?? "{}"));
  }
});

try {
  token = (
    await (
      await fetch(`${API}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "dev-manager", password: "dev-test-123456" }),
      })
    ).json()
  ).access_token;
  const created = await api("POST", "/api/v1/menu-items", {
    name: oldName,
    unit_price: "150",
    unit_cost: "40",
    category: "咖啡",
    sort_order: 0,
  });
  ok("API 建立品項（$150、成本 $40、咖啡）", created.status === 201, `HTTP ${created.status}`);
  const itemId = created.body.id;

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/menu`, { waitUntil: "networkidle" });

  await page.getByRole("button", { name: `${oldName} 編輯` }).click();
  const dialog = page.getByRole("dialog", { name: `編輯 ${oldName}` });
  await dialog.waitFor();
  ok(
    "編輯視窗帶出現有內容",
    (await dialog.getByLabel("品名").inputValue()) === oldName &&
      (await dialog.getByLabel("分類").inputValue()) === "咖啡" &&
      (await dialog.getByLabel("售價").inputValue()) === "150" &&
      (await dialog.getByLabel("成本").inputValue()) === "40",
  );

  // 售價 0：擋下不送
  await dialog.getByLabel("售價").fill("0");
  await dialog.getByRole("button", { name: "儲存" }).click();
  const alert = dialog.getByRole("alert");
  await alert.waitFor();
  ok("售價 0 擋下、不送出", (await alert.textContent()) === "售價須為正整數元" && patches.length === 0);

  await dialog.getByLabel("品名").fill(newName);
  await dialog.getByLabel("分類").fill(newCategory);
  await dialog.getByLabel("售價").fill("170");
  await dialog.getByLabel("成本").fill("");
  await dialog.getByLabel("介紹").fill("燕麥奶替代鮮奶");
  await page.screenshot({ path: join(SHOTS, "01-edit-dialog.png"), fullPage: true });
  await dialog.getByRole("button", { name: "儲存" }).click();
  await dialog.waitFor({ state: "detached" });
  ok(
    "一次送出改過的欄位（成本清空送 null）",
    patches.length === 1 &&
      JSON.stringify(patches[0]) ===
        JSON.stringify({
          name: newName,
          category: newCategory,
          unit_price: "170",
          unit_cost: null,
          description: "燕麥奶替代鮮奶",
        }),
    JSON.stringify(patches),
  );
  const row = page.locator("tr", { hasText: newName });
  await row.waitFor();
  ok("清單顯示新品名與新價、成本未填", (await row.textContent()).includes("170") && (await row.textContent()).includes("未填"));
  await page.screenshot({ path: join(SHOTS, "02-menu-list.png"), fullPage: true });

  const saved = (await api("GET", "/api/v1/menu-items")).body.find((i) => i.id === itemId);
  ok(
    "後端已更新",
    saved.name === newName &&
      saved.category === newCategory &&
      saved.unit_price === "170" &&
      saved.unit_cost === null &&
      saved.description === "燕麥奶替代鮮奶",
    JSON.stringify(saved),
  );

  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector(".pos-menu-tiles");
  const tab = page.getByRole("tab", { name: newCategory });
  if ((await tab.count()) > 0) await tab.click();
  const tile = page.locator(".pos-menu-tile").filter({ hasText: newName });
  await tile.waitFor();
  ok("POS 餐飲磚顯示新品名與 $170", (await tile.textContent()).includes("170"), (await tile.textContent()) ?? "");
  await page.screenshot({ path: join(SHOTS, "03-pos-tile.png"), fullPage: true });
} catch (err) {
  ok("流程執行", false, String(err));
  await page.screenshot({ path: join(SHOTS, "99-error.png"), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
