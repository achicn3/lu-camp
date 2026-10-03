// 排隊收購「詳細」按折數帶出收購價（店主 2026-10-04）。報到 2 件 →
// 1 號：「詳細」填原價、按 5 折 → 收購價自動帶出建議價 → 存檔 → 列上的收購價跟著更新、後端 deal_cost＝suggested_cost；
// 2 號：列上先填 300 → 「詳細」先顯示 300 → 填原價、按折數 → 照新折數重算、蓋掉 300（同收購頁）→ 存檔 → 後端是建議價；
// 再開「詳細」手動改成 240 → 存檔 → 後端是 240（手動的保留）。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/intake-detail-cost-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "intake-detail-cost");
const RUN = String(Date.now()).slice(-6);
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 820, height: 1180 }, hasTouch: true });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/acquisition/intake`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /建立新賣方/ }).click();
  await page.getByLabel("姓名", { exact: true }).fill(`詳細估價賣家-${RUN}`);
  await page.getByLabel("手機", { exact: true }).fill(uniquePhone());
  await page.getByLabel("身分證字號", { exact: true }).fill(validNationalId());
  await page.getByRole("button", { name: "建立並選取" }).click();
  await page.getByLabel("收購幾件").fill("2");
  await page.getByRole("button", { name: "報到，發號碼" }).click();
  await page.waitForURL(/\/acquisition\/intake\/\d+/);
  const batchId = Number(/\/acquisition\/intake\/(\d+)/.exec(page.url())?.[1]);
  const token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
  const batchApi = async () =>
    (await fetch(`${API}/api/v1/intake-batches/${batchId}`, { headers: { Authorization: `Bearer ${token}` } })).json();

  // 1 號：「詳細」填原價、按 5 折 → 收購價自動帶出
  await page.getByRole("button", { name: "1 號 詳細" }).click();
  const form1 = page.getByRole("form", { name: /修改第 1 列/ });
  await form1.getByLabel("原價／件").fill("1000");
  await form1.getByRole("button", { name: "5折" }).click();
  const filled = await form1.getByLabel("收購價／件").inputValue();
  ok("1 號詳細：按折數自動帶出收購價", /^\d+$/.test(filled) && Number(filled) > 0, filled);
  await page.screenshot({ path: join(SHOTS, "01-detail-suggested.png") });
  await form1.getByRole("button", { name: "儲存詳細" }).click();
  await page.waitForFunction(
    (value) => document.querySelector('input[aria-label="1 號 收購價"]')?.value === value,
    filled,
    { timeout: 8000 },
  );
  ok("1 號：存檔後列上的收購價跟著更新", true, filled);
  let saved = await batchApi();
  ok(
    "1 號後端：deal_cost＝suggested_cost＝帶出的價、預計售價 500",
    saved.lines[0].deal_cost === filled &&
      saved.lines[0].suggested_cost === filled &&
      saved.lines[0].expected_listed_price === "500",
    JSON.stringify([saved.lines[0].deal_cost, saved.lines[0].suggested_cost, saved.lines[0].expected_listed_price]),
  );

  // 2 號：列上已填 300 → 詳細按折數照收購頁重算蓋掉
  await page.getByLabel("2 號 收購價").fill("300");
  await page.getByLabel("2 號 收購價").press("Enter");
  await page.getByText(/已填 2／2 件/).waitFor({ timeout: 8000 });
  await page.getByRole("button", { name: "2 號 詳細" }).click();
  const form2 = page.getByRole("form", { name: /修改第 2 列/ });
  ok("2 號詳細：先顯示列上的 300", (await form2.getByLabel("收購價／件").inputValue()) === "300");
  await form2.getByLabel("原價／件").fill("1000");
  await form2.getByRole("button", { name: "5折" }).click();
  ok("2 號詳細：按折數照收購頁重算、蓋掉 300", (await form2.getByLabel("收購價／件").inputValue()) === filled);
  await page.screenshot({ path: join(SHOTS, "02-detail-recomputed.png") });
  await form2.getByRole("button", { name: "儲存詳細" }).click();
  await page.waitForFunction(
    (value) => document.querySelector('input[aria-label="2 號 收購價"]')?.value === value,
    filled,
    { timeout: 8000 },
  );
  saved = await batchApi();
  ok(
    "2 號後端：收購價＝建議價、原價與折數有存",
    saved.lines[1].deal_cost === filled && saved.lines[1].reference_price === "1000" && saved.lines[1].discount_pct === 50,
    JSON.stringify([saved.lines[1].deal_cost, saved.lines[1].reference_price, saved.lines[1].discount_pct]),
  );
  // 詳細裡手動改價：保留手動的
  const deal2 = form2.getByLabel("收購價／件");
  if (!(await deal2.isVisible().catch(() => false))) await page.getByRole("button", { name: "2 號 詳細" }).click();
  await page.getByRole("form", { name: /修改第 2 列/ }).getByLabel("收購價／件").fill("240");
  await page.getByRole("form", { name: /修改第 2 列/ }).getByRole("button", { name: "儲存詳細" }).click();
  await page.waitForFunction(
    () => document.querySelector('input[aria-label="2 號 收購價"]')?.value === "240",
    null,
    { timeout: 8000 },
  );
  saved = await batchApi();
  ok("2 號後端：詳細手動改的 240 有存", saved.lines[1].deal_cost === "240", saved.lines[1].deal_cost);
  await page.screenshot({ path: join(SHOTS, "03-saved.png"), fullPage: true });
  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  ok("流程例外", false, String(error));
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
if (failures.length > 0) process.exitCode = 1;
