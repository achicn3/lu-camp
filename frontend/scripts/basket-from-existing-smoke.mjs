// 販售籃從現有散裝開籃／加入（店主 2026-10-10：豬尾巴後來想共用標籤）瀏覽器 E2E：
// 1) API 收三筆豬尾巴散裝：$29×5、$29×7、$35×3 → 庫存「販售籃」按「開新販售籃」→ 搜尋、勾兩筆 $29
//    （名稱與售價帶第一筆；$35 那筆不能勾、講出它的價錢）→ 建立 → 籃子共 12 件。
// 2) 再收一筆 $29×4 → 籃子「加入現有散裝」→ 勾它 → 加入 → 共 16 件。
// 3) 後端：舊散裝標籤指到這一籃（POS 掃舊標籤就賣整籃）。
// 需 backend + frontend 已起、已 seed（dev-manager）。會建立收購與販售籃，請指向隔離測試庫：
//   SMOKE_ALLOW_WRITE=1 node scripts/basket-from-existing-smoke.mjs
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "basket-from-existing");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立收購與販售籃，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });
const RUN = String(Date.now()).slice(-5);
const ITEM = `豬尾巴-${RUN}`;

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

let token = "";
let idem = 0;
async function api(method, path, body) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "Idempotency-Key": `basket-existing-${RUN}-${(idem += 1)}`,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
}

let seller = null;
async function acquireLot(qty, price) {
  const resp = await api("POST", "/api/v1/acquisitions", {
    type: "BULK_LOT",
    contact_id: seller,
    lot: {
      name: ITEM,
      acquisition_cost: String(qty * 10),
      acquisition_basis: "UNSPECIFIED",
      total_qty: qty,
      unit_price: String(price),
    },
  });
  assert.equal(resp.status, 201, JSON.stringify(resp.body));
  return resp.body.lot_code;
}

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
page.on("pageerror", (err) => ok("頁面沒有 JS 錯誤", false, String(err)));

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
  if ((await api("GET", "/api/v1/cash-sessions/current")).body === null) {
    await api("POST", "/api/v1/cash-sessions/open", { opening_float: "5000" });
  }
  seller = (
    await api("POST", "/api/v1/contacts", {
      name: `散裝賣家-${RUN}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
    })
  ).body.id;
  const lotA = await acquireLot(5, 29);
  const lotB = await acquireLot(7, 29);
  const lotC = await acquireLot(3, 35);
  ok("API：收三筆豬尾巴散裝（$29×5、$29×7、$35×3）", true, `${lotA} ${lotB} ${lotC}`);

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  await page.click('[role="tab"]:has-text("販售籃")');

  // 1) 開新販售籃
  await page.getByRole("button", { name: "開新販售籃" }).click();
  const form = page.getByRole("form", { name: "開新販售籃" });
  await form.getByLabel("搜尋散裝").fill(ITEM);
  await form.getByRole("button", { name: "找散裝" }).click();
  await form.getByText(lotA).waitFor();
  await form.getByRole("checkbox", { name: new RegExp(lotA) }).check();
  ok(
    "名稱與售價帶第一筆",
    (await form.getByLabel("販售籃名稱").inputValue()) === ITEM &&
      (await form.getByLabel("每件售價").inputValue()) === "29",
  );
  await form.getByRole("checkbox", { name: new RegExp(lotB) }).check();
  ok("售價不同的那筆不能勾", await form.getByRole("checkbox", { name: new RegExp(lotC) }).isDisabled());
  await page.screenshot({ path: join(SHOTS, "01-new-basket.png"), fullPage: true });
  await form.getByRole("button", { name: "建立販售籃" }).click();
  const notice = page.getByRole("status").filter({ hasText: "已開販售籃" });
  await notice.waitFor();
  ok("提示已開籃、請印標籤", ((await notice.textContent()) ?? "").includes("請印籃子標籤"), (await notice.textContent()) ?? "");
  const row = page.locator("tr", { hasText: ITEM }).first();
  await row.waitFor();
  ok("籃子共 12 件", ((await row.textContent()) ?? "").includes("12"), (await row.textContent()) ?? "");

  // 2) 再收一筆，加入現有散裝
  const lotD = await acquireLot(4, 29);
  await row.getByRole("button", { name: "加入現有散裝" }).click();
  const addForm = page.getByRole("form", { name: `${ITEM} 加入現有散裝` });
  await addForm.getByLabel("搜尋散裝").fill(ITEM);
  await addForm.getByRole("button", { name: "找散裝" }).click();
  await addForm.getByText(lotD).waitFor();
  ok("已在籃裡的兩筆不再列出", (await addForm.getByText(lotA).count()) === 0 && (await addForm.getByText(lotB).count()) === 0);
  await addForm.getByRole("checkbox", { name: new RegExp(lotD) }).check();
  await page.screenshot({ path: join(SHOTS, "02-add-lot.png"), fullPage: true });
  await addForm.getByRole("button", { name: "加入這 1 筆" }).click();
  await page.getByRole("status").filter({ hasText: "已把 1 筆散裝加入" }).waitFor();
  await page.locator("tr", { hasText: ITEM }).first().filter({ hasText: "16" }).waitFor();
  ok("加入後共 16 件", true);
  await page.screenshot({ path: join(SHOTS, "03-basket-16.png"), fullPage: true });

  // 3) 舊散裝標籤指到這一籃
  const baskets = (await api("GET", `/api/v1/bulk-baskets?q=${encodeURIComponent(ITEM)}`)).body;
  const basket = baskets.find((b) => b.name === ITEM);
  const old = (await api("GET", `/api/v1/bulk-lots/by-code/${lotA}`)).body;
  ok(
    "後端：三筆來源在同一籃、舊標籤指到這一籃",
    basket?.sources.length === 3 && old?.basket_id === basket?.id,
    JSON.stringify({ sources: basket?.sources.length, basket: basket?.id, old: old?.basket_id }),
  );
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
