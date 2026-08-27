// 雙印表機角色分離煙霧（ADR-018）：從交易紀錄實際按「補印明細聯」「重印出餐單」，
// 驗證列印工作真的送到 hardware-agent，且**沒有**流到發票專屬機。
//
// 為什麼是補印而不是重新結帳：本腳本跑在店家的實測庫上，補印不新增銷售、只留下一筆
// 列印稽核；驗的是「去哪一台」，不需要一筆新交易。發票證明聯的補印會真的呼叫 Amego
// （消耗一次補印額度、且會印出某位客人的發票），故**不由自動化腳本觸發**，改以
// hardware-agent 端點直接驗（見 docs/15 §4.1）。
//
// 執行（backend:8000 + frontend:3000 已起，另起一台 real 模式 agent，docs/20）：
//   SMOKE_AGENT_URL=http://127.0.0.1:8011 node frontend/scripts/dual-printer-smoke.mjs
// SMOKE_AGENT_URL 未設時不改寫，直接打前端預設的 :8001。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = strip(process.env.SMOKE_BASE ?? "http://localhost:3000");
const API_BASE = strip(process.env.SMOKE_API_BASE ?? "http://localhost:8000");
const AGENT_FROM = strip(process.env.SMOKE_AGENT_FROM ?? "http://localhost:8001");
const AGENT_TO = process.env.SMOKE_AGENT_URL ? strip(process.env.SMOKE_AGENT_URL) : null;
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "dual-printer");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}
function strip(v) {
  return v.replace(/\/+$/, "");
}

async function apiJson(path, { token = null, expected = [200] } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const data = await res.json().catch(() => null);
  if (!expected.includes(res.status)) {
    throw new Error(`GET ${path} → ${res.status}: ${JSON.stringify(data?.detail ?? data)}`);
  }
  return data;
}

let browser;
try {
  const loginRes = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: USERNAME, password: PASSWORD }),
  });
  const token = (await loginRes.json()).access_token;
  const list = await apiJson("/api/v1/sales?limit=50", { token });
  const sales = Array.isArray(list) ? list : (list.items ?? []);
  const completed = sales.find((s) => s.status === "COMPLETED");
  const dineOrTakeout = sales.find((s) => s.status === "COMPLETED" && s.service_mode);
  if (!completed) throw new Error("交易紀錄中找不到已完成的銷售，無法驗補印");

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  // 列印請求改打 real 模式的代理；**只改 host/port**，路徑與 body 原樣送出。
  const printCalls = [];
  await page.route("**/print/**", async (route) => {
    const url = route.request().url();
    printCalls.push({ url, body: route.request().postData() });
    if (AGENT_TO && url.startsWith(AGENT_FROM)) {
      await route.continue({ url: AGENT_TO + url.slice(AGENT_FROM.length) });
    } else {
      await route.continue();
    }
  });

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("帳號").fill(USERNAME);
  await page.getByLabel("密碼").fill(PASSWORD);
  await page.getByRole("button", { name: "登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  ok("登入成功", true);

  await page.goto(`${BASE}/sales`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: `補印銷售 ${completed.id} 商品明細聯` }).waitFor();
  await page.screenshot({ path: join(SHOTS, "01-sales.png"), fullPage: false });

  // ── 明細聯：收據機 ──
  await page.getByRole("button", { name: `補印銷售 ${completed.id} 商品明細聯` }).click();
  await page.getByText(`已送出 #${completed.id} 的商品明細聯`).waitFor({ timeout: 20000 });
  ok("補印明細聯送出成功（收據機）", true, `#${completed.id}`);
  await page.screenshot({ path: join(SHOTS, "02-detail-printed.png") });

  // ── 出餐單：同樣走收據機（本店未接第三台出餐機）──
  if (dineOrTakeout) {
    await page.getByRole("button", { name: `重印銷售 ${dineOrTakeout.id} 出餐單` }).click();
    await page.getByText(`已送出 #${dineOrTakeout.id} 的出餐單`).waitFor({ timeout: 20000 });
    ok("重印出餐單送出成功（收據機）", true, `#${dineOrTakeout.id}`);
    await page.screenshot({ path: join(SHOTS, "03-kitchen-printed.png") });
  } else {
    ok("重印出餐單送出成功（收據機）", false, "找不到有餐飲的銷售，此項未驗");
  }

  // ── 去向斷言：兩張紙都不得走發票端點 ──
  const paths = printCalls.map((c) => new URL(c.url).pathname);
  ok("明細聯打 /print/detail", paths.includes("/print/detail"), paths.join(" "));
  ok(
    "沒有任何一次列印誤打發票端點",
    !paths.some((p) => p === "/print/einvoice" || p === "/print/raw"),
    paths.join(" "),
  );
} catch (err) {
  ok(`未預期錯誤：${err.message}`, false);
} finally {
  await browser?.close();
}

const passed = results.filter((r) => r.pass).length;
console.log(`\n${passed}/${results.length} 通過`);
process.exit(passed === results.length ? 0 : 1);
