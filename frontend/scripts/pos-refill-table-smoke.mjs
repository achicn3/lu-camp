// 續點要「本桌點過」（店主 2026-10-19）POS 真瀏覽器煙霧：真 backend＋真 Postgres＋真瀏覽器。
// 先用 API 在 A1 賣一杯（一般點），再到 POS 點同一項的續點：
//   內用 A1 → 顯示「本桌 90 分鐘內點過」；內用 B2（沒點過）→ 提醒但照樣能結；外帶 → 擋下「外帶不能續點」。
// 再打開「不限品項都可以續點」：沒勾可續點的品項也能續點，A1（有消費過）確認、B2（沒消費）提醒。結束後關回去。
// 需：後端 SMOKE_API（預設 http://localhost:8131/api/v1）、POS SMOKE_POS（預設 http://localhost:3131），
// 隔離的測試資料庫（會改桌號、續點折數設定並新增一個品項）。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const API = process.env.SMOKE_API ?? "http://localhost:8131/api/v1";
const POS = process.env.SMOKE_POS ?? "http://localhost:3131";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/pos-refill-table-shots";
mkdirSync(SHOTS, { recursive: true });

let token = "";
async function api(method, path, body, headers = {}) {
  const r = await fetch(API + path, {
    method,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { ok: r.ok, status: r.status, body: r.status === 204 ? null : await r.json() };
}
const must = async (...args) => {
  const r = await api(...args);
  assert.ok(r.ok, `${args[0]} ${args[1]}: ${r.status} ${JSON.stringify(r.body)}`);
  return r.body;
};
token = (await must("POST", "/auth/login", { username: "dev-manager", password: PASSWORD })).access_token;
await must("PATCH", "/settings", { dine_in_tables: ["A1", "B2"], menu_refill_pct: 80, menu_refill_any_item: false });
await api("POST", "/cash-sessions/open", { opening_float: "1000" }); // 已經開帳就沿用
const NAME = `煙霧美式${Date.now() % 100000}`;
const item = await must("POST", "/menu-items", { name: NAME, unit_price: "120", refillable: true });
const CAKE = `煙霧蛋糕${Date.now() % 100000}`;
await must("POST", "/menu-items", { name: CAKE, unit_price: "100" }); // 沒勾可續點

// A1 先點一杯（一般點）
const sold = await api(
  "POST",
  "/sales",
  {
    lines: [{ line_type: "MENU", menu_item_id: item.id, qty: 1, menu_option_ids: [] }],
    service_mode: "DINE_IN",
    table_no: "A1",
    tenders: [{ tender_type: "CASH", amount: "120" }],
  },
  { "Idempotency-Key": `refill-table-${Date.now()}` },
);
assert.ok(sold.ok, `seed sale: ${sold.status} ${JSON.stringify(sold.body)}`);
const recent = await must("GET", "/sales/dine-in-recent-items?table_no=A1");
assert.ok(recent.menu_item_ids.includes(item.id), "A1 點過這項");

const browser = await chromium.launch();
try {
  const pos = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  await skipOpeningCheckRedirect(pos);
  await pos.goto(`${POS}/login`);
  await pos.locator('input[name="username"]').fill("dev-manager");
  await pos.locator('input[name="password"]').fill(PASSWORD);
  await pos.getByRole("button", { name: "登入", exact: true }).click();
  await pos.waitForURL((url) => !url.pathname.startsWith("/login"));
  await pos.goto(`${POS}/pos`);
  await pos.locator(".pos-menu-tile").filter({ hasText: NAME }).click();
  const pick = pos.getByRole("dialog", { name: `加入 ${NAME}` });
  await pick.getByRole("checkbox", { name: "續點（8 折，加料也打折）" }).check();
  await pick.getByRole("button", { name: "加入購物車" }).click();
  await pos.getByRole("button", { name: `移除 續點・${NAME}` }).waitFor();

  const warning = pos.locator(".pos-refill-warning");
  // 內用 A1：本桌點過 → 不提醒
  await pos.getByRole("radio", { name: "內用" }).click();
  await pos.getByRole("radiogroup", { name: "桌號" }).getByRole("radio", { name: "A1" }).click();
  await pos.locator(".pos-refill-found").filter({ hasText: `本桌 90 分鐘內點過：續點・${NAME}` }).waitFor();
  assert.equal(await warning.count(), 0, "A1 點過，不提醒");
  await pos.screenshot({ path: `${SHOTS}/01-dine-in-a1-no-warning.png` });

  // 內用 B2：本桌沒點過 → 提醒、但不擋
  await pos.getByRole("radiogroup", { name: "桌號" }).getByRole("radio", { name: "B2" }).click();
  await warning.filter({ hasText: `本桌 90 分鐘內沒點過：續點・${NAME}。確定是續點再結帳。` }).waitFor();
  assert.equal(await pos.getByText("外帶不能續點，請把續點改成一般點，或改選內用").count(), 0);
  await pos.screenshot({ path: `${SHOTS}/02-dine-in-b2-warning.png` });

  // 外帶：擋下
  await pos.getByRole("radio", { name: "外帶" }).click();
  await pos.getByText("外帶不能續點，請把續點改成一般點，或改選內用").waitFor();
  assert.equal(await warning.count(), 0);
  await pos.screenshot({ path: `${SHOTS}/03-takeout-blocked.png` });

  // 不限品項續點：菜單頁打開設定
  await pos.goto(`${POS}/menu`, { waitUntil: "networkidle" });
  await pos.getByText("目前續點打 8 折", { exact: false }).waitFor();
  const anyBox = pos.getByRole("checkbox", { name: /不限品項都可以續點/ });
  await anyBox.click();
  await pos.getByText("目前不限品項：每個餐飲品項都能續點。").waitFor();
  await pos.screenshot({ path: `${SHOTS}/04-setting-any-item.png` });
  await pos.goto(`${POS}/pos`, { waitUntil: "networkidle" });
  await pos.locator(".pos-menu-tile").filter({ hasText: CAKE }).waitFor();
  const leftover = pos.getByRole("button", { name: `移除 續點・${NAME}` }); // 購物車若還留著上一段的續點就移除
  if (await leftover.count()) await leftover.click();
  await pos.locator(".pos-menu-tile").filter({ hasText: CAKE }).click();
  const cakePick = pos.getByRole("dialog", { name: `加入 ${CAKE}` });
  await cakePick.getByRole("checkbox", { name: "續點（8 折，加料也打折）" }).check();
  await cakePick.getByRole("button", { name: "加入購物車" }).click();
  await pos.getByRole("radio", { name: "內用" }).click();
  await pos.getByRole("radiogroup", { name: "桌號" }).getByRole("radio", { name: "A1" }).click();
  await pos.locator(".pos-refill-found").filter({ hasText: `本桌 90 分鐘內有消費過，可以續點：續點・${CAKE}` }).waitFor();
  await pos.screenshot({ path: `${SHOTS}/05-any-item-a1.png` });
  await pos.getByRole("radiogroup", { name: "桌號" }).getByRole("radio", { name: "B2" }).click();
  await warning.filter({ hasText: `本桌 90 分鐘內沒有消費紀錄：續點・${CAKE}。確定是續點再結帳。` }).waitFor();
  await pos.getByRole("button", { name: `移除 續點・${CAKE}` }).click();
  console.log("PASS A1 顯示點過 → B2 提醒不擋 → 外帶擋下 → 不限品項：沒勾可續點也能續點、A1 有消費確認、B2 提醒");
} finally {
  await must("PATCH", "/settings", { menu_refill_any_item: false }).catch(() => {});
  await browser.close();
}
