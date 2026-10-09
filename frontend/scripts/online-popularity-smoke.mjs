// 人氣標籤煙霧（docs/63 §7 M2b）：POS 賣出 → 後台人氣榜看得到 → 不重新發佈，幾秒後客人頁品項出現「人氣 No.1」；
// 後台關掉 → 客人頁標籤消失。結束時把人氣設定還原、建的品項封存。
// 需 backend :8114（含線上點餐設定、開帳）＋ wrangler :8799 ＋ frontend :3500。只准對隔離測試環境執行（SMOKE_ALLOW_WRITE=1）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium, devices } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const BASE = process.env.SMOKE_BASE ?? "http://localhost:3500";
const API = process.env.SMOKE_API ?? "http://localhost:8114";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8799";
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/online-popularity");
mkdirSync(SHOTS, { recursive: true });

let token = "";
async function api(method, path, body, headers = {}) {
  const response = await fetch(`${API}/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}
async function must(method, path, body, headers) {
  const result = await api(method, path, body, headers);
  assert.ok(result.status < 300, `${method} ${path}: ${result.status} ${JSON.stringify(result.body)}`);
  return result.body;
}
async function waitFor(fn, label, ms = 45000) {
  const deadline = Date.now() + ms;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`等不到：${label}`);
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}
const results = [];
const ok = (name, pass, detail = "") => {
  results.push(pass);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
};

token = (await must("POST", "/auth/login", { username: USERNAME, password: PASSWORD })).access_token;
const run = randomUUID().slice(0, 6);
const category = `人氣測試-${run}`;
const name = `招牌拿鐵-${run}`;
const item = await must("POST", "/menu-items", { name, unit_price: "150", unit_cost: "30", category });
const original = await must("GET", "/online-order/popularity");
await must("PUT", "/online-order/popularity", { is_active: true, window_days: 30, min_qty: 10 });
await api("POST", "/cash-sessions/open", { opening_float: "1000" });
const settings = await must("GET", "/settings");
await must("PATCH", "/settings", { dine_in_tables: [...new Set([...(settings.dine_in_tables ?? []), "A1"])] });
await must("POST", "/online-order/publish");
const code = (await must("GET", "/online-order/status")).tables.find((t) => t.label === "A1")?.code;
await must("PUT", "/online-orders/accepting", { accepting: true });

const browser = await chromium.launch();
const desk = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
const errors = [];
desk.on("pageerror", (e) => errors.push(`後台：${e}`));
const guestCtx = await browser.newContext({ ...devices["iPhone 13"] });
const guest = await guestCtx.newPage();
guest.on("pageerror", (e) => errors.push(`客人頁：${e}`));
const card = () => guest.locator("article.item", { hasText: name });
async function guestLabels() {
  await guest.goto(`${ORDER}/t/${code}`, { waitUntil: "networkidle" });
  await guest.getByRole("button", { name: category }).first().click().catch(() => {});
  return (await card().count()) ? card().innerText() : "";
}
try {
  ok("還沒賣：客人頁沒有人氣標籤", !(await guestLabels()).includes("人氣"));

  // POS 賣出 10 份（剛好達門檻）——不重新發佈
  await must("POST", "/sales", {
    lines: [{ line_type: "MENU", menu_item_id: item.id, qty: 10 }],
    tenders: [{ tender_type: "CASH", amount: "1500" }],
    service_mode: "TAKEOUT",
  }, { "Idempotency-Key": `pop-${run}` });

  await skipOpeningCheckRedirect(desk, BASE);
  await desk.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await desk.fill('input[name="username"]', USERNAME);
  await desk.fill('input[name="password"]', PASSWORD);
  await desk.click('button:has-text("登入")');
  await desk.waitForURL(`${BASE}/`);
  await desk.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  await desk.getByRole("tab", { name: "線上發布" }).click();
  const board = desk.getByRole("list", { name: "目前的人氣榜" });
  await board.getByText(`${category}：${name} 人氣 No.1（10 份）`).waitFor();
  ok("後台人氣榜：這個分類第一名、10 份", true);
  await desk.locator("section", { hasText: "人氣標籤" }).last().screenshot({ path: join(SHOTS, "01-admin.png") });

  const text = await waitFor(async () => {
    const labels = await guestLabels();
    return labels.includes("人氣 No.1") ? labels : null;
  }, "客人頁出現人氣 No.1");
  ok("不重新發佈，客人頁幾秒內出現「人氣 No.1」", true, text.replace(/\n/g, " "));
  await card().screenshot({ path: join(SHOTS, "02-guest-label.png") });

  // 後台關掉 → 標籤消失
  await desk.getByLabel("在線上菜單顯示人氣標籤").uncheck();
  await desk.getByRole("button", { name: "儲存人氣設定" }).click();
  await desk.getByText("已儲存，幾秒內客人頁就會更新。").waitFor();
  await waitFor(async () => !(await guestLabels()).includes("人氣"), "客人頁標籤消失");
  ok("後台關掉後客人頁的人氣標籤消失", true);

  const overflow = await guest.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  ok("手機不橫向捲動", overflow <= 0, `溢出 ${overflow}px`);
  ok("頁面無 JS 例外", errors.length === 0, errors.join(" / "));
} catch (error) {
  await guest.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true }).catch(() => {});
  ok("流程跑完", false, String(error));
} finally {
  await api("PUT", "/online-order/popularity", {
    is_active: original.is_active, window_days: original.window_days, min_qty: original.min_qty,
  });
  await api("DELETE", `/menu-items/${item.id}`);
  await browser.close();
}
const failed = results.filter((r) => !r).length;
console.log(`\n${results.length - failed}/${results.length} PASS；截圖 ${SHOTS}`);
process.exit(failed === 0 ? 0 : 1);
