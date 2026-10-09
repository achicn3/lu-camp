// 「不知道喝什麼」引導推薦煙霧（docs/63 §2 M2a）：後台每個答案勾品項 → 發佈 → 客人手機回答三題 →
// 最推薦＋備選、可直接加入；重新回答換答案換推薦。結束時把問答設定還原、建的品項封存。
// 需 backend :8114（含線上點餐設定）＋ wrangler :8799 ＋ frontend :3500。只准對隔離測試環境執行（SMOKE_ALLOW_WRITE=1）。
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
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/online-quiz");
mkdirSync(SHOTS, { recursive: true });

let token = "";
async function api(method, path, body) {
  const response = await fetch(`${API}/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}
async function must(method, path, body) {
  const result = await api(method, path, body);
  assert.ok(result.status < 300, `${method} ${path}: ${result.status} ${JSON.stringify(result.body)}`);
  return result.body;
}
const results = [];
const ok = (name, pass, detail = "") => {
  results.push(pass);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
};

token = (await must("POST", "/auth/login", { username: USERNAME, password: PASSWORD })).access_token;
const run = randomUUID().slice(0, 6);
const names = { fruity: `果香拿鐵-${run}`, nutty: `堅果拿鐵-${run}`, cake: `戚風-${run}` };
const created = {};
for (const [key, name] of Object.entries(names)) {
  created[key] = (await must("POST", "/menu-items", {
    name, unit_price: key === "cake" ? "90" : "160", unit_cost: "30", category: key === "cake" ? "甜點" : "咖啡",
  })).id;
}
const original = await must("GET", "/online-order/quiz");
const settings = await must("GET", "/settings");
await must("PATCH", "/settings", { dine_in_tables: [...new Set([...(settings.dine_in_tables ?? []), "A1"])] });
// 從乾淨的預設三題開始勾，結果才固定（同一個資料庫重跑時不會混進上一輪的品項）
const fresh = original.is_default ? original : { ...original, questions: original.questions.map((q) => ({
  ...q, options: q.options.map((o) => ({ ...o, items: [] })) })) };
await must("PUT", "/online-order/quiz", { is_active: false, questions: fresh.questions });

const browser = await chromium.launch();
const desk = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
const errors = [];
desk.on("pageerror", (e) => errors.push(`後台：${e}`));
try {
  await skipOpeningCheckRedirect(desk, BASE);
  await desk.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await desk.fill('input[name="username"]', USERNAME);
  await desk.fill('input[name="password"]', PASSWORD);
  await desk.click('button:has-text("登入")');
  await desk.waitForURL(`${BASE}/`);

  // ① 後台：每個答案勾適合的品項
  await desk.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  await desk.getByRole("tab", { name: "線上發布" }).click();
  await desk.getByRole("link", { name: "設定問答" }).click();
  await desk.waitForURL(/\/menu\/quiz$/);
  const pick = async (label, name) =>
    desk.getByLabel(`${label} 加入品項`).selectOption({ label: name });
  await pick("第 1 題答案 1", names.fruity);
  await pick("第 1 題答案 1", names.nutty);
  await pick("第 1 題答案 2", names.cake);
  await pick("第 2 題答案 1", names.fruity);
  await pick("第 2 題答案 2", names.nutty);
  await pick("第 3 題答案 1", names.fruity);
  await pick("第 3 題答案 2", names.nutty);
  await desk.getByLabel("在客人頁顯示「不知道喝什麼？」").check();
  await desk.screenshot({ path: join(SHOTS, "01-admin-quiz.png"), fullPage: true });
  await desk.getByRole("button", { name: "儲存" }).click();
  await desk.waitForURL(/\/menu\?section=online$/);
  const summary = desk.locator("section", { hasText: "不知道喝什麼？（引導推薦）" });
  await summary.getByText(/顯示中・3 題・勾了 3 個品項/).waitFor();
  ok("後台存好：摘要顯示中、3 題、勾了 3 個品項", true);

  await must("POST", "/online-order/publish");
  const code = (await must("GET", "/online-order/status")).tables.find((t) => t.label === "A1")?.code;
  await must("PUT", "/online-orders/accepting", { accepting: true });

  // ② 客人：回答三題 → 最推薦＋備選
  const guestCtx = await browser.newContext({ ...devices["iPhone 13"] });
  const guest = await guestCtx.newPage();
  guest.on("pageerror", (e) => errors.push(`客人頁：${e}`));
  await guest.goto(`${ORDER}/t/${code}`, { waitUntil: "networkidle" });
  await guest.getByRole("button", { name: "不知道喝什麼？" }).click();
  const quiz = guest.locator("#quiz");
  ok("首頁入口打開問答，第 1 / 3 題", (await quiz.innerText()).includes("第 1 / 3 題"));
  await guest.screenshot({ path: join(SHOTS, "02-guest-question.png") });
  await quiz.getByRole("button", { name: "咖啡", exact: true }).click();
  await quiz.getByRole("button", { name: "果香、明亮" }).click();
  await quiz.getByRole("button", { name: "黑咖啡" }).click();
  const text = await quiz.innerText();
  const main = text.indexOf(names.fruity);
  ok("最推薦是被勾最多次的果香拿鐵，備選有堅果拿鐵",
    text.includes("最推薦") && main >= 0 && text.includes(names.nutty) && main < text.indexOf(names.nutty),
    text.replace(/\n/g, " ").slice(0, 160));
  await guest.screenshot({ path: join(SHOTS, "03-guest-result.png") });
  ok("出結果後「最推薦」在畫面內、沒被上方固定列擋住", await guest.getByText("最推薦").evaluate((e) => e.getBoundingClientRect().top > 80));
  await quiz.getByRole("button", { name: `加入：${names.fruity}` }).click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).waitFor();
  ok("推薦結果可以直接加入購物車", true);

  await quiz.getByRole("button", { name: "重新回答" }).click();
  await quiz.getByRole("button", { name: "想吃甜的" }).click();
  await quiz.getByRole("button", { name: "順口不苦" }).click();
  await quiz.getByRole("button", { name: "加牛奶" }).click();
  const second = await quiz.innerText();
  ok("重新回答換成甜的：最推薦戚風", second.includes("最推薦") && second.includes(names.cake) && !second.includes(names.fruity),
    second.replace(/\n/g, " ").slice(0, 120));

  const overflow = await guest.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  ok("手機不橫向捲動", overflow <= 0, `溢出 ${overflow}px`);
  ok("頁面無 JS 例外", errors.length === 0, errors.join(" / "));
} catch (error) {
  await desk.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true }).catch(() => {});
  ok("流程跑完", false, String(error));
} finally {
  await api("PUT", "/online-order/quiz", { is_active: original.is_active, questions: original.questions.map((q) => ({
    ...q, options: q.options.map((o) => ({ ...o, items: o.items.filter((r) => !Object.values(created).includes(r.id) || r.kind !== "item") })) })) });
  for (const id of Object.values(created)) await api("DELETE", `/menu-items/${id}`);
  await browser.close();
}
const failed = results.filter((r) => !r).length;
console.log(`\n${results.length - failed}/${results.length} PASS；截圖 ${SHOTS}`);
process.exit(failed === 0 ? 0 : 1);
