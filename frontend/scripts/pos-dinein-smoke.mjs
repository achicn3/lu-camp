// docs/35 餐飲內用/外帶與桌號 + 出餐單 瀏覽器煙霧測試。
//
// 需 backend(:8000) + frontend(:3000) + hardware-agent(:8001) 已起。
// 前置資料（開帳、餐飲品項）由本腳本自行以 API 補齊，不依賴先跑過別的腳本。
// 執行：SMOKE_BASE=http://localhost:3000 node scripts/pos-dinein-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "pos-dinein");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const RUN = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
const ITEM_NAME = `煙霧-手沖咖啡-${RUN}`;
const TABLES = ["A1", "A2"];

// ── 前置：以 API 補齊開帳與餐飲品項 ──
async function api(token, method, path, body, extraHeaders = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...extraHeaders,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

const login = await api(null, "POST", "/api/v1/auth/login", {
  username: "dev-manager",
  password: "dev-test-123456",
});
if (login.status !== 200) {
  ok("API 登入", false, `HTTP ${login.status}`);
  process.exit(1);
}
const token = login.json.access_token;

const session = await api(token, "GET", "/api/v1/cash-sessions/current");
if (session.json === null || session.status === 404) {
  const opened = await api(token, "POST", "/api/v1/cash-sessions/open", {
    opening_float: "1000",
  });
  ok("開帳（前置）", opened.status < 400, `HTTP ${opened.status}`);
} else {
  ok("開帳（前置）", true, "已開帳");
}

const created = await api(
  token,
  "POST",
  "/api/v1/menu-items",
  { name: ITEM_NAME, unit_price: "180" },
  { "Idempotency-Key": `smoke-dinein-${RUN}` },
);
ok("建立餐飲品項（前置）", created.status < 400, `HTTP ${created.status}`);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 950 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  // ── 1. 設定頁：維護桌號清單 ──
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  await page.waitForSelector(".dinein-card");
  for (const table of TABLES) {
    const existing = page.locator(`.dinein-table-list li:has-text("${table}")`);
    if ((await existing.count()) === 0) {
      await page.getByLabel("新增桌號").fill(table);
      await page.click('button:has-text("新增桌號")');
    }
  }
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SHOTS}/01-settings-tables.png` });
  await page.click('button:has-text("儲存餐飲內用設定")');
  await page.waitForSelector("text=餐飲內用設定已儲存", { timeout: 10000 });
  ok("設定頁維護桌號清單", true, TABLES.join("/"));

  // 重複桌號要被擋（前端先行驗證，與後端同一組規則）
  await page.getByLabel("新增桌號").fill(TABLES[0]);
  await page.click('button:has-text("新增桌號")');
  // Next.js 的 route announcer 也是 role=alert；限定在卡片內找，否則 strict mode 撞兩個。
  const dupAlert = page.locator('.dinein-card [role="alert"]');
  await dupAlert.waitFor();
  ok("重複桌號被擋", ((await dupAlert.textContent()) ?? "").includes("已存在"));
  await page.screenshot({ path: `${SHOTS}/02-settings-duplicate-blocked.png` });

  // ── 2. POS：加入餐飲 → 內用/外帶面板出現 ──
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector(".pos-menu-tiles");
  const tile = page.locator(`.pos-menu-tile:has-text("${ITEM_NAME}")`).first();
  await tile.click();
  const qtyDialog = page.locator('[role="dialog"]');
  await qtyDialog.waitFor();
  await qtyDialog.getByRole("button", { name: "加入購物車" }).click();
  await page.waitForSelector(".pos-dinein-panel");
  ok("加入餐飲後出現內用/外帶面板", true);

  // 未選 → 結帳鍵停用且說明原因
  const checkout = page.locator(".pos-checkout");
  const errorText = (await page.locator(".pos-dinein-error").textContent()) ?? "";
  ok(
    "未選內用/外帶 → 結帳停用並說明原因",
    (await checkout.isDisabled()) && errorText.includes("內用或外帶"),
    errorText,
  );
  await page.screenshot({ path: `${SHOTS}/03-pos-mode-required.png` });

  // 內用 → 未選桌號仍擋
  await page.click('.pos-dinein-mode:has-text("內用")');
  await page.waitForSelector(".pos-dinein-tables");
  ok(
    "選內用後未選桌號 → 仍擋",
    (await checkout.isDisabled()) &&
      ((await page.locator(".pos-dinein-error").textContent()) ?? "").includes("桌號"),
  );
  await page.screenshot({ path: `${SHOTS}/04-pos-table-required.png` });

  // 選桌號 → 可結帳
  await page.click(`.pos-dinein-table:has-text("${TABLES[1]}")`);
  await page.waitForSelector(".pos-checkout:not([disabled])", { timeout: 15000 });
  ok("選桌號後可結帳", true, TABLES[1]);
  await page.screenshot({ path: `${SHOTS}/05-pos-table-selected.png` });

  // ── 3. 結帳 → 自動印出餐單 ──
  await page.click(".pos-checkout");
  await page.waitForSelector(".pos-complete", { timeout: 30000 });
  await page.waitForTimeout(1500);
  const kitchenText = (await page.locator(".pos-complete").textContent()) ?? "";
  ok(
    "完成畫面顯示出餐單已送出列印",
    kitchenText.includes("出餐單已送出列印") && kitchenText.includes(TABLES[1]),
    kitchenText.includes("列印失敗") ? "出餐單列印失敗（代理未起？）" : "",
  );
  ok("提供重印出餐單", (await page.locator(".pos-kitchen-reprint").count()) > 0);
  await page.screenshot({ path: `${SHOTS}/06-pos-complete-kitchen.png` });

  // 結帳後會跳「列印商品明細？」對話框，其 backdrop 會攔截點擊——先關掉才碰得到重印鍵。
  await page.click('[aria-label="列印商品明細"] button:has-text("不用，完成")');
  await page.waitForSelector('[aria-label="列印商品明細"]', { state: "detached" });

  // 重印可用
  await page.click(".pos-kitchen-reprint");
  await page.waitForTimeout(1500);
  ok(
    "重印出餐單成功",
    !((await page.locator(".pos-complete").textContent()) ?? "").includes("列印失敗"),
  );

  // ── 4. 交易紀錄顯示桌號 ──
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.waitForSelector("table tbody tr");
  const firstRow = (await page.locator("table tbody tr").first().textContent()) ?? "";
  ok("交易紀錄顯示桌號", firstRow.includes(TABLES[1]), firstRow.slice(0, 80));
  await page.screenshot({ path: `${SHOTS}/07-sales-table-no.png` });

  // docs/35 §3.2 的第二個重印入口：離開 POS 後吧台沒收到單，這裡是唯一補得回來的地方。
  await page.locator('button[aria-label^="重印銷售"]').first().click();
  await page.waitForSelector("text=的出餐單。", { timeout: 20000 });
  ok("交易紀錄可重印出餐單", true);
  await page.screenshot({ path: `${SHOTS}/07b-sales-reprint.png` });

  // ── 5. 外帶：不需桌號 ──
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector(".pos-menu-tiles");
  await page.locator(`.pos-menu-tile:has-text("${ITEM_NAME}")`).first().click();
  const qtyDialog2 = page.locator('[role="dialog"]');
  await qtyDialog2.waitFor();
  await qtyDialog2.getByRole("button", { name: "加入購物車" }).click();
  await page.waitForSelector(".pos-dinein-panel");
  await page.click('.pos-dinein-mode:has-text("外帶")');
  await page.waitForSelector(".pos-checkout:not([disabled])", { timeout: 15000 });
  ok(
    "外帶不需桌號即可結帳",
    (await page.locator(".pos-dinein-tables").count()) === 0,
  );
  await page.screenshot({ path: `${SHOTS}/08-pos-takeout.png` });
  await page.click(".pos-checkout");
  await page.waitForSelector(".pos-complete", { timeout: 30000 });
  await page.waitForTimeout(1500);
  ok(
    "外帶完成畫面出餐單標示外帶",
    ((await page.locator(".pos-complete").textContent()) ?? "").includes("外帶"),
  );
  await page.click('[aria-label="列印商品明細"] button:has-text("不用，完成")');
  await page.waitForSelector('[aria-label="列印商品明細"]', { state: "detached" });
  await page.screenshot({ path: `${SHOTS}/09-pos-takeout-complete.png` });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
