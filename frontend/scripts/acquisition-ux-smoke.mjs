// 收購頁操作速度改善煙霧（2026-09-23）：
// ④ 買斷兩件 → 第一件自動收合成一行摘要、底部固定摘要列顯示件數與應付
// ② 送出後自動送印標籤（以 route 代替硬體代理的 /print/label，攔下實際送出的張數）
// ③ 「繼續收這位賣方」→ 賣方保留、直接收下一筆
// ⑤ 補印憑證聯／作廢收購收在頁尾「更多操作」
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/acquisition-ux-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "acquisition-ux");
const RUN = Date.now();
const SELLER = `林賣家-${String(RUN).slice(-5)}`;
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(path, { method = "GET", token, body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  return response.json();
}

const CATEGORY = `露營用品-${String(RUN).slice(-5)}`;
let categoryCreated = false;

async function fillRow(page, index, { name, cost, listed }) {
  const row = page.locator(".acq-rows .acq-row").nth(index);
  const category = row.getByLabel("分類", { exact: true });
  await category.click();
  await category.fill(CATEGORY);
  if (categoryCreated) {
    await page.getByRole("option", { name: CATEGORY, exact: true }).click();
  } else {
    await page.click(`button:has-text("建立「${CATEGORY}」")`);
    categoryCreated = true;
  }
  await row.locator('summary:has-text("品名")').click();
  await row.getByLabel("品名", { exact: true }).fill(name);
  await row.getByLabel("成色").selectOption("A");
  await row.getByLabel("收購價", { exact: true }).fill(String(cost));
  await row.getByLabel("上架售價（含稅與手續費）", { exact: true }).fill(String(listed));
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
const printed = [];
// 代替櫃檯硬體代理：跨來源請求要回 CORS 標頭，預檢（OPTIONS）也要放行，否則瀏覽器會卡住。
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
await page.route("**/print/label", async (route) => {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS });
    return;
  }
  printed.push(route.request().postDataJSON());
  await route.fulfill({ status: 200, headers: CORS, contentType: "application/json", body: '{"ok":true}' });
});

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  const current = await fetch(`${API}/api/v1/cash-sessions/current`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!current.ok || !(await current.json())) {
    await apiJson("/api/v1/cash-sessions/open", { method: "POST", token, body: { opening_float: "5000" } });
  }

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  const firstCard = page.locator(".acq > .card").first();
  ok("頁面一打開就是收購表單（補印憑證聯不在最上面）", !(await firstCard.innerText()).includes("補印收購憑證聯"));
  await page.screenshot({ path: join(SHOTS, "01-top.png"), fullPage: false });

  // 賣方
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', SELLER);
  await page.fill('input[aria-label="手機"]', uniquePhone(RUN));
  await page.fill('input[aria-label="身分證字號"]', validNationalId(RUN));
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector(`text=${SELLER}`);

  // 兩件：第一件填好 → 新增一列 → 第一件收合
  await fillRow(page, 0, { name: "Snow Peak 焚火台 M", cost: 900, listed: 2000 });
  await page.getByRole("button", { name: "＋ 新增一列" }).click();
  const collapsed = page.getByRole("button", { name: /編輯第 1 列/ });
  await collapsed.waitFor();
  ok("第一件收合成一行摘要", (await collapsed.innerText()).includes("焚火台"), await collapsed.innerText());
  await fillRow(page, 0, { name: "Coleman 營燈", cost: 300, listed: 800 });
  const bar = page.getByRole("region", { name: "收購摘要" });
  const barText = await bar.innerText();
  ok("底部摘要列顯示件數與應付", barText.includes("共 2 件") && barText.includes("1,200"), barText.replace(/\s+/g, " "));
  await page.screenshot({ path: join(SHOTS, "02-two-rows.png"), fullPage: true });

  // 送出 → 自動送印
  await bar.getByRole("button", { name: "送出收購" }).click();
  await page.waitForSelector("text=收購完成");
  await page.getByText(/已自動送出列印/).waitFor({ timeout: 15000 });
  ok("送出後自動送印 2 張標籤", printed.length === 2, `攔到 ${printed.length} 張`);
  await page.screenshot({ path: join(SHOTS, "03-done-auto-print.png"), fullPage: true });

  // 繼續收這位賣方
  await page.getByRole("button", { name: /繼續收這位賣方/ }).click();
  await page.getByText(SELLER).first().waitFor();
  ok("繼續收同一位賣方，不必重新搜尋", (await page.locator("text=收購完成").count()) === 0);
  await page.screenshot({ path: join(SHOTS, "04-continue-seller.png"), fullPage: false });

  // 更多操作
  const more = page.locator("details.acq-more");
  await more.locator("summary").click();
  ok("補印憑證聯收在「更多操作」", await more.getByRole("heading", { name: "補印收購憑證聯" }).isVisible());
  ok("作廢收購收在「更多操作」", await more.getByRole("heading", { name: /作廢收購/ }).isVisible());
  await page.screenshot({ path: join(SHOTS, "05-more-actions.png"), fullPage: true });

  // 手機寬度
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.screenshot({ path: join(SHOTS, "06-mobile.png"), fullPage: false });
  ok("手機寬度摘要列可見", await page.getByRole("region", { name: "收購摘要" }).isVisible());

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  ok("流程例外", false, String(error));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exitCode = failed > 0 ? 1 : 0;
