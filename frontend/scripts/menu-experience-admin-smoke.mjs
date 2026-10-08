// 手沖體驗卡後台煙霧（docs/63 §4、M1c）：真 backend／Postgres ＋ 真管理頁。
// 菜單 →「線上發布」分頁 →「新增體驗卡」另開一頁（選原品項、預選豆子、看到售價與卡面預覽、改包含內容、
// 選配色與動畫）→ 存好回到列表 → 編輯頁停用 → 刪除要再確認；品項「線上呈現」可設加購角色。
// 375／1280px 不橫向捲動。
// 只准對隔離測試環境執行（SMOKE_ALLOW_WRITE=1）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/menu-experience-admin");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
mkdirSync(SHOTS, { recursive: true });

let token = "";
async function api(method, path, body) {
  const response = await fetch(`${API}/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  assert.ok(response.ok, `${method} ${path}: ${response.status} ${text}`);
  return text ? JSON.parse(text) : null;
}
const results = [];
const ok = (name, pass, detail = "") => {
  results.push(pass);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
};

token = (await api("POST", "/auth/login", { username: USERNAME, password: PASSWORD })).access_token;
const run = randomUUID().slice(0, 6);
const brewName = `手沖咖啡-${run}`;
const brew = await api("POST", "/menu-items", { name: brewName, unit_price: "220", category: `手沖-${run}` });
const beans = await api("POST", "/menu-option-groups", {
  name: `豆子-${run}`, min_select: 1, max_select: 1,
  options: [{ name: "蜜桃蹦蹦", price_delta: "60" }, { name: "天堂鳥莊園", price_delta: "20" }],
});
const temp = await api("POST", "/menu-option-groups", {
  name: `溫度-${run}`, min_select: 1, max_select: 1, options: [{ name: "熱" }, { name: "冰" }],
});
await api("PUT", `/menu-items/${brew.id}/option-groups`, { group_ids: [beans.id, temp.id] });
const title = `蜜桃蹦蹦體驗-${run}`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
try {
  await skipOpeningCheckRedirect(page, BASE);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "線上發布" }).click();
  const section = page.getByRole("region", { name: "手沖體驗卡" });
  await section.waitFor();
  ok("掃碼網址表收起來，列表一眼看得到體驗卡", await page.locator("details.online-publish-tables").evaluate((d) => !d.open));
  await section.getByRole("link", { name: "新增體驗卡" }).click();
  await page.waitForURL(/\/menu\/experiences\/new$/);
  const form = page.getByRole("form", { name: "體驗卡" });
  await form.getByLabel("原品項").selectOption({ label: brewName });
  await form.getByLabel("蜜桃蹦蹦（+$60）").check();
  const hint = await form.getByText(/售價/).innerText();
  ok("售價提示＝原品項＋預選，並提醒客人還要選溫度", hint.includes("$280") && hint.includes(`溫度-${run}`), hint);
  await form.getByLabel("卡片標題").fill(title);
  await form.getByLabel("標籤").fill("清甜果香");
  await form.getByLabel("產地／處理法").fill("柯契爾｜水洗");
  await form.getByLabel("風味").fill("水蜜桃・白桃・荔枝");
  ok("新卡帶入三項包含內容", (await form.getByLabel("包含項目 3").inputValue()) === "現場體驗");
  const preview = page.getByRole("article", { name: "卡面預覽" });
  ok("卡面預覽即時顯示標題與價格", (await preview.innerText()).includes(title) && (await preview.innerText()).includes("$280 起"));
  await form.getByLabel("抽卡動畫").selectOption("truck");
  await form.getByLabel("蜜桃粉").check();
  await page.screenshot({ path: join(SHOTS, "01-form.png"), fullPage: true });
  await form.getByRole("button", { name: "儲存體驗卡" }).click();
  await page.waitForURL(/\/menu\?section=online$/);
  const row = section.locator("li", { hasText: title });
  await row.waitFor();
  ok("存好回到「線上發布」分頁", (await page.getByRole("tab", { name: "線上發布" }).getAttribute("aria-selected")) === "true");
  ok("列表出現：品項與預選豆子", (await row.innerText()).includes(`${brewName} · 蜜桃蹦蹦`));
  const saved = (await api("GET", "/online-order/experiences")).find((e) => e.title === title);
  ok("後端存的是品項＋預選，沒有價格欄位", saved?.menu_item_id === brew.id && saved.option_ids.length === 1 && !("price" in saved));
  await page.screenshot({ path: join(SHOTS, "02-list.png"), fullPage: true });

  await row.getByRole("link", { name: "編輯" }).click();
  await page.waitForURL(/\/menu\/experiences\/\d+$/);
  const editForm = page.getByRole("form", { name: "體驗卡" });
  ok("編輯頁帶入原本的內容", (await editForm.getByLabel("卡片標題").inputValue()) === title);
  await editForm.getByLabel(/啟用/).uncheck();
  await editForm.getByRole("button", { name: "儲存體驗卡" }).click();
  await page.waitForURL(/\/menu\?section=online$/);
  await row.filter({ hasText: "停用中" }).waitFor();
  ok("可以停用", true);

  await row.getByRole("button", { name: "刪除" }).click();
  ok("刪除要再確認", (await row.getByRole("button", { name: "確定刪除" }).count()) === 1);
  await row.getByRole("button", { name: "確定刪除" }).click();
  await row.waitFor({ state: "detached" });
  ok("刪除後從列表消失", true);

  // 品項「線上呈現」設加購角色
  await page.getByRole("tab", { name: "品項" }).click();
  await page.getByRole("button", { name: `${brewName} 線上呈現` }).click();
  const dialog = page.getByRole("dialog", { name: `${brewName} 的線上呈現` });
  await dialog.getByLabel("加購角色").selectOption("experience");
  await dialog.getByRole("button", { name: "儲存設定" }).click();
  await dialog.waitFor({ state: "detached" }).catch(() => {});
  const presentation = await api("GET", `/online-order/menu-items/${brew.id}/presentation`);
  ok("加購角色存好了", presentation.role === "experience");

  // 手機寬度
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`${BASE}/menu/experiences/new`, { waitUntil: "networkidle" });
  await page.getByRole("form", { name: "體驗卡" }).waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  ok("手機寬度不橫向捲動", overflow <= 0, `溢出 ${overflow}px`);
  await page.screenshot({ path: join(SHOTS, "03-phone-form.png"), fullPage: true });
  ok("頁面無 JS 例外", errors.length === 0, errors.join(" | "));
} catch (error) {
  ok("流程例外", false, String(error));
  await page.screenshot({ path: join(SHOTS, "zz-error.png"), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}
const failed = results.filter((pass) => !pass).length;
console.log(`\n${results.length - failed}/${results.length} PASS；截圖 ${SHOTS}`);
process.exit(failed ? 1 : 0);
