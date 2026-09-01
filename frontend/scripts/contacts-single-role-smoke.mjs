// 身分簡化煙霧：建檔不問角色、收購自動打標、遷移過的舊資料照常可用。
//
// **必須對遷移過的資料庫跑**：migration 把 CONSIGNOR 併進 SELLER、替每個人補上 MEMBER，
// 但「資料改對了」不等於「畫面還能用」——舊資料在新程式下渲染失敗、或收購流程找不到
// 賣方，都是只有真的點下去才會發現的。
//
// 執行（backend:8000 對已 migrate 的庫、frontend:3000 已起、已開帳）：
//   node frontend/scripts/contacts-single-role-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "single-role");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const RUN = String(Date.now()).slice(-6);

mkdirSync(SHOTS, { recursive: true });
const results = [];
const ok = (n, p, d = "") => { results.push({ n, p }); console.log(`${p ? "✅" : "❌"} ${n}${d ? `：${d}` : ""}`); };

async function apiJson(path, { method = "GET", token, body } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}: ${await res.text()}`);
  return res.json();
}

const { access_token: token } = await apiJson("/api/v1/auth/login", {
  method: "POST", body: { username: USERNAME, password: PASSWORD },
});

// 前置：遷移過的庫裡不該再有 CONSIGNOR，而且每個人都該是會員
const sample = await apiJson("/api/v1/contacts?limit=200", { token });
const rows = Array.isArray(sample) ? sample : sample.items ?? [];
ok("遷移後的資料沒有殘留的 CONSIGNOR",
  !rows.some((c) => (c.roles ?? []).includes("CONSIGNOR")), `抽樣 ${rows.length} 筆`);
ok("遷移後每個人都是會員",
  rows.every((c) => (c.roles ?? []).includes("MEMBER")), `抽樣 ${rows.length} 筆`);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

  // 1) 建檔頁：沒有角色勾選；只填姓名電話就能建
  await page.goto(`${BASE}/contacts`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "會員 / 賣方" }).first().waitFor({ timeout: 15000 });
  ok("建檔表單沒有角色勾選框", (await page.getByText("角色", { exact: true }).count()) === 0);

  const memberName = `純會員-${RUN}`;
  const memberPhone = uniquePhone(RUN);
  const form = page.locator("form").filter({ has: page.getByRole("button", { name: "建檔" }) });
  await form.getByLabel("姓名").fill(memberName);
  await form.getByLabel("電話").fill(memberPhone);
  await page.getByRole("button", { name: "建檔" }).click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: join(SHOTS, "01-create-member.png") });

  const created = (await apiJson(`/api/v1/contacts?q=${encodeURIComponent(memberName)}`, { token }))
    .find((c) => c.name === memberName);
  ok("不填身分證字號也能建檔（純消費會員）", Boolean(created));
  ok("新建檔的身分就是「會員」",
    created && JSON.stringify(created.roles) === JSON.stringify(["MEMBER"]),
    JSON.stringify(created?.roles));

  // 2) 這位會員去賣東西 → 補登身分證字號 → 收購成立後自動變成賣方
  await page.goto(`${BASE}/acquisition`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "收購" }).first().waitFor({ timeout: 15000 });
  ok("收購頁不再區分「賣方／寄售人」",
    (await page.getByText("寄售人").count()) === 0);

  await page.getByPlaceholder("以手機或姓名搜尋").fill(memberPhone);
  await page.getByRole("button", { name: new RegExp(memberName) }).first()
    .waitFor({ state: "visible", timeout: 10000 });
  await page.getByRole("button", { name: new RegExp(memberName) }).first().click();
  await page.waitForTimeout(800);

  const nidBox = page.getByLabel("身分證字號").first();
  ok("既有會員缺身分證字號時出現補登欄", (await nidBox.count()) > 0);
  await nidBox.fill(validNationalId(RUN));
  await page.getByRole("button", { name: /補登身分證字號/ }).click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: join(SHOTS, "02-backfill.png") });

  // **補登不等於賣過東西**：這一刻收購還沒成立，店員按取消就該什麼都沒發生。
  // 若在此就標成賣方，帳面上他賣過東西、實際上一次都沒有（Codex 對抗式審查 高）。
  const afterBackfill = await apiJson(`/api/v1/contacts/${created.id}`, { token });
  ok("補登身分證字號後**仍只是會員**（收購尚未成立）",
    JSON.stringify(afterBackfill.roles) === JSON.stringify(["MEMBER"]),
    JSON.stringify(afterBackfill.roles));

  // 真的完成一次收購 → 這時才該變成賣方
  await page.getByLabel("品名").first().fill(`身分測試品-${RUN}`);
  await page.locator(".acq-row select").first().selectOption("A");
  const cat = page.getByLabel("分類");
  await cat.click();
  await cat.fill(`身分分類-${RUN}`);
  await page.click(`button:has-text("建立「身分分類-${RUN}」")`);
  await page.getByLabel("上架售價（含稅）").fill("1000");
  await page.getByLabel("收購價").fill("300");
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 20000 });
  await page.screenshot({ path: join(SHOTS, "025-after-acquisition.png") });

  const afterAcq = await apiJson(`/api/v1/contacts/${created.id}`, { token });
  ok("收購成立後才變成「會員 + 賣方」",
    ["MEMBER", "SELLER"].every((r) => afterAcq.roles.includes(r)),
    JSON.stringify(afterAcq.roles));

  // 3) 詳情頁：身分是唯讀的
  await page.goto(`${BASE}/contacts/${created.id}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  const header = await page.locator(".member-head-roles").innerText().catch(() => "");
  ok("詳情頁標題顯示身分「會員、賣方」", header.includes("會員") && header.includes("賣方"), header);

  // **必須切到編輯分頁再驗**：角色編輯器本來就只在那一頁，在總覽頁數「沒有更新角色按鈕」
  // 無論拿掉與否都會是 0——那種綠燈證明不了任何事。
  await page.getByRole("button", { name: "編輯" }).click();
  await page.waitForTimeout(1200);
  const editText = await page.locator("body").innerText();
  ok("編輯頁確實載入（看得到身分證字號區塊）", editText.includes("身分證字號"));
  ok("編輯頁沒有「更新角色」按鈕（身分由系統管理）",
    (await page.getByRole("button", { name: "更新角色" }).count()) === 0);
  ok("編輯頁沒有角色勾選框",
    (await page.locator('input[type="checkbox"]').count()) === 0);
  ok("編輯頁以唯讀方式顯示身分", editText.includes("會員、賣方"),
    editText.match(/身分[^\n]*\n?[^\n]*/)?.[0]?.replace(/\n/g, " ") ?? "");
  await page.screenshot({ path: join(SHOTS, "03-detail-readonly.png") });
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.p);
console.log(`\n${results.length - failed.length}/${results.length} 通過；截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
