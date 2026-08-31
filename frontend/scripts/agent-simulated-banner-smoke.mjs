// 假裝模式橫幅煙霧（三層地雷防線的第三層）：真代理 vs 假裝代理，畫面必須說得出差別。
//
// 為什麼要瀏覽器測而不只單元測：這個橫幅的價值全在「店員真的會看到」。單元測只證明
// 條件對，證不了它在 POS 頁上被別的元素蓋住、或縮在角落沒人看見。
//
// 執行（backend:8000 + frontend:3000 已起）：
//   真機代理跑 :8001（AGENT_DEVICES=real）、假裝代理跑 :8011（AGENT_DEVICES=fake）
//   node frontend/scripts/agent-simulated-banner-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const strip = (u) => u.replace(/\/+$/, "");
const BASE = strip(process.env.SMOKE_BASE ?? "http://localhost:3000");
// 前端在瀏覽器端呼叫的代理位址寫在 .env.local（WSL 下是 WSL IP，不是 localhost）；
// 改寫路由必須用**前端實際會打的那個位址**，否則規則對不上、悄悄什麼都沒攔到。
const AGENT_REAL = strip(
  process.env.SMOKE_AGENT_REAL ?? process.env.NEXT_PUBLIC_AGENT_URL ?? "http://localhost:8001",
);
const AGENT_FAKE = strip(process.env.SMOKE_AGENT_FAKE ?? "http://localhost:8011");
// real 模式但個別裝置沒設 host（標籤機是選配）——整組旗標會謊報「都是真的」的那個情境。
const AGENT_MIXED = strip(process.env.SMOKE_AGENT_MIXED ?? "http://localhost:8012");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "agent-sim-banner");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const BANNER = "測試模式";

mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("帳號").fill(USERNAME);
  await page.getByLabel("密碼").fill(PASSWORD);
  await page.getByRole("button", { name: "登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 });
}

/** 讓瀏覽器把代理請求導到指定的代理位址（前端建置時的 :8001 是寫死的）。 */
async function routeAgent(context, target) {
  let hits = 0;
  await context.route(`${AGENT_REAL}/**`, async (route) => {
    hits += 1;
    const url = route.request().url().replace(AGENT_REAL, target);
    await route.continue({ url });
  });
  return () => hits;
}

/** 記錄代理 /health 的實際結果：沒問到就談不上「沒有橫幅代表接了真機」。 */
function watchHealth(page) {
  const seen = [];
  page.on("response", (res) => {
    if (res.url().includes("/health")) seen.push(res.status());
  });
  page.on("requestfailed", (req) => {
    if (req.url().includes("/health")) seen.push(`failed:${req.failure()?.errorText}`);
  });
  return () => seen;
}

async function bannerVisible(page) {
  // 橫幅每 60 秒輪詢一次，首輪在掛載時就發出；給它幾秒。
  try {
    await page.getByText(BANNER).first().waitFor({ state: "visible", timeout: 8000 });
    return true;
  } catch {
    return false;
  }
}

const browser = await chromium.launch();
try {
  // 前置：兩台代理都要活著，否則後面的「有/沒有橫幅」全都不算數
  for (const [label, url, want] of [
    ["真機", AGENT_REAL, []],
    ["假裝", AGENT_FAKE, ["標籤機", "收據機", "錢櫃"]],
    ["混合（標籤機沒設）", AGENT_MIXED, ["標籤機"]],
  ]) {
    const res = await fetch(`${url}/health`);
    const body = await res.json();
    ok(
      `前置：${label}代理點名 ${want.join("、") || "（無）"}`,
      JSON.stringify(body.simulated_devices) === JSON.stringify(want),
      JSON.stringify(body),
    );
  }

  // 情境一：接真機 → 不得出現橫幅（誤報會讓警告變成背景雜訊）
  {
    const context = await browser.newContext();
    const page = await context.newPage();
    const health = watchHealth(page);
    await login(page);
    await page.goto(`${BASE}/pos`, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: /POS/ }).first().waitFor({ timeout: 15000 });
    const shown = await bannerVisible(page);
    // 「沒有橫幅」必須是因為問到了 simulated=false，不能是因為根本沒問到（CORS 擋掉、
    // 代理沒起來都會讓查詢失敗而同樣不顯示橫幅）——那種綠燈是為了錯的理由成立的。
    ok("真的問到代理健康狀態（非被擋下）", health().includes(200), JSON.stringify(health()));
    ok("接真機的代理：POS 頁沒有測試模式橫幅", shown === false);
    await page.screenshot({ path: join(SHOTS, "01-real-no-banner.png"), fullPage: false });
    await context.close();
  }

  // 情境二：假裝模式 → 橫幅出現，而且要說出後果（不會出紙），不是只寫模式名稱
  {
    const context = await browser.newContext();
    const agentHits = await routeAgent(context, AGENT_FAKE);
    const page = await context.newPage();
    const health = watchHealth(page);
    await login(page);
    await page.goto(`${BASE}/pos`, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: /POS/ }).first().waitFor({ timeout: 15000 });
    const shown = await bannerVisible(page);
    ok("真的問到假裝代理健康狀態（非被擋下）", health().includes(200), JSON.stringify(health()));
    // 先確認改寫規則真的攔到了——否則「沒橫幅」只是規則沒對上，不是功能壞掉
    ok("改寫規則有攔到代理請求", agentHits() > 0, `${agentHits()} 次`);
    ok("假裝模式代理：POS 頁出現測試模式橫幅", shown);
    if (shown) {
      const text = await page.getByText(BANNER).first().innerText();
      ok("橫幅講出後果（按了不會有東西出來）", /不會有東西出來/.test(text), text.trim());
      // 店員一眼要看得到：橫幅須落在首屏內
      const box = await page.getByText(BANNER).first().boundingBox();
      const vh = page.viewportSize().height;
      ok("橫幅在首屏可見", box !== null && box.y >= 0 && box.y < vh, box ? `y=${Math.round(box.y)}` : "無");
    }
    await page.screenshot({ path: join(SHOTS, "02-fake-banner.png"), fullPage: false });
    await context.close();
  }
  // 情境三（Codex 高風險項）：real 模式但標籤機沒設 host。橫幅必須**只**點名標籤機——
  // 說成「全部都是測試模式」，店員會以為連結帳都不能做而停止營業。
  {
    const context = await browser.newContext();
    const agentHits = await routeAgent(context, AGENT_MIXED);
    const page = await context.newPage();
    const health = watchHealth(page);
    await login(page);
    await page.goto(`${BASE}/pos`, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: /POS/ }).first().waitFor({ timeout: 15000 });
    const shown = await bannerVisible(page);
    ok("真的問到混合代理健康狀態（非被擋下）", health().includes(200), JSON.stringify(health()));
    ok("改寫規則有攔到代理請求", agentHits() > 0, `${agentHits()} 次`);
    ok("混合配置：橫幅出現", shown);
    if (shown) {
      const text = await page.getByText(BANNER).first().innerText();
      ok("橫幅只點名標籤機", /標籤機/.test(text) && !/收據機|錢櫃/.test(text), text.trim());
    }
    await page.screenshot({ path: join(SHOTS, "03-mixed-label-only.png"), fullPage: false });
    await context.close();
  }
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過；截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
