// 交易紀錄歷史查單煙霧（QA BUG-003）：客人隔天回來退貨，店員得找得到昨天那筆。
// 驗：預設只看今日 → 昨天的單看不到 → 用交易編號查得到 → 用日期範圍也查得到
//     → 而且找回來之後那一列真的能操作（補印明細聯按得下去）。
// 執行：node scripts/sales-history-search-smoke.mjs（backend:8000 + frontend:3000 已起）
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = strip(process.env.SMOKE_BASE ?? "http://localhost:3000");
const API_BASE = strip(process.env.SMOKE_API_BASE ?? "http://localhost:8000");
const DB = process.env.SMOKE_DB ?? "lucamp_manual";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "sales-history");
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
function psql(sql) {
  return execFileSync("docker", ["exec", "lu-camp-db-1", "psql", "-U", "lucamp", "-d", DB, "-tAc", sql], {
    encoding: "utf8",
  }).trim();
}
async function api(path, { method = "GET", token, body, headers = {}, expect = [200] } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!expect.includes(res.status)) {
    throw new Error(`${method} ${path} → ${res.status}: ${JSON.stringify(data)?.slice(0, 300)}`);
  }
  return data;
}

let browser;
try {
  const login = await api("/api/v1/auth/login", {
    method: "POST",
    body: { username: USERNAME, password: PASSWORD },
  });
  const token = login.access_token;
  if ((await api("/api/v1/cash-sessions/current", { token })) === null) {
    await api("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
      expect: [201],
    });
  }

  // 建一筆交易，再把它的時間搬到昨天——模擬「客人隔天回來」。
  const runId = Date.now();
  const seller = await api("/api/v1/contacts", {
    method: "POST",
    token,
    expect: [201],
    body: {
      name: `SMOKE_HIST_${runId}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
      source_note: "sales history smoke",
    },
  });
  const acq = await api("/api/v1/acquisitions", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `SMOKE_HIST_ACQ_${runId}` },
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      note: "sales history smoke",
      items: [
        { name: `SMOKE_HIST_ITEM_${runId}`, grade: "A", listed_price: "300", acquisition_cost: "120" },
      ],
    },
  });
  const sale = await api("/api/v1/sales", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `SMOKE_HIST_SALE_${runId}` },
    body: {
      lines: [{ line_type: "SERIALIZED", item_code: acq.item_codes[0], qty: 1 }],
      tenders: [{ tender_type: "CASH", amount: "300" }],
    },
  });
  psql(`UPDATE sales SET created_at = now() - interval '1 day' WHERE id = ${Number(sale.id)}`);
  const movedTo = psql(`SELECT created_at::date FROM sales WHERE id = ${Number(sale.id)}`);

  // **對照組**：另一筆留在今天。少了它，「回今日」只驗到舊列消失——今日清單整個
  // 讀取失敗也會通過；日期範圍也只驗起日，迄日壞掉照樣全綠（Codex 審查）。
  const todayAcq = await api("/api/v1/acquisitions", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `SMOKE_HIST_ACQ2_${runId}` },
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      note: "sales history smoke (today)",
      items: [
        { name: `SMOKE_HIST_TODAY_${runId}`, grade: "A", listed_price: "300", acquisition_cost: "120" },
      ],
    },
  });
  const todaySale = await api("/api/v1/sales", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `SMOKE_HIST_SALE2_${runId}` },
    body: {
      lines: [{ line_type: "SERIALIZED", item_code: todayAcq.item_codes[0], qty: 1 }],
      tenders: [{ tender_type: "CASH", amount: "300" }],
    },
  });
  ok(
    "前置：一筆移到昨天、一筆留在今天",
    Boolean(movedTo) && Boolean(todaySale.id),
    `舊 #${sale.id} → ${movedTo}；今日 #${todaySale.id}`,
  );

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("帳號").fill(USERNAME);
  await page.getByLabel("密碼").fill(PASSWORD);
  await page.getByRole("button", { name: "登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

  await page.goto(`${BASE}/sales`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: /交易紀錄/ }).waitFor({ timeout: 20000 });

  // 以**整列**定位而不是某顆按鈕：作廢之後該列的按鈕會變（補印就消失了），
  // 用按鈕當「這筆在不在畫面上」的判準會在後面誤判。
  // **限定在交易清單那張表**：店長畫面還有 LINE Pay 待對帳表，用全頁 tbody tr
  // 會把它的列一起算進來而誤紅（Codex 第二輪）。
  const salesRows = page.locator("table.sales-list tbody tr");
  const row = salesRows.filter({ hasText: String(sale.id) });
  const todayRow = salesRows.filter({ hasText: String(todaySale.id) });
  await todayRow.first().waitFor({ timeout: 20000 });
  ok(
    "預設（今日）看得到今天那筆、看不到昨天那筆",
    (await row.count()) === 0 && (await todayRow.count()) === 1,
  );
  await page.screenshot({ path: join(SHOTS, "01-today.png") });

  // 用交易編號查
  await page.getByLabel("交易編號").fill(String(sale.id));
  await page.getByRole("button", { name: "查詢" }).click();
  await row.first().waitFor({ timeout: 20000 });
  // **只能有那一列**：若單號查詢壞成回傳全部歷史交易，只驗「目標列存在」照樣會過。
  const idModeRows = await salesRows.count();
  ok("用交易編號查得到、且結果只有那一筆", idModeRows === 1, `列數 ${idModeRows}`);
  await page.screenshot({ path: join(SHOTS, "02-by-id.png") });

  // 找回來之後**真的能操作**——不能只是看得到。用「作廢」而不是「補印」來驗：
  // 補印要印表機在線，那是環境條件；作廢不需要硬體，而且正是 QA 報告講的那個情境
  // （客人隔天回來要退貨/作廢，店員在畫面上做不到）。
  await page.getByRole("button", { name: `作廢銷售 ${sale.id}` }).click();
  await page.getByRole("dialog", { name: "作廢銷售確認" }).waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "確認作廢", exact: true }).click();
  await page.getByText(`銷售 #${sale.id} 已作廢`).waitFor({ timeout: 30000 });
  ok("找回來的舊單可以直接作廢（不需印表機）", true);
  await page.screenshot({ path: join(SHOTS, "03-acted.png") });

  // 日期範圍也要查得到
  await page.getByRole("button", { name: "回今日" }).click();
  await todayRow.first().waitFor({ timeout: 20000 });
  ok(
    "按「回今日」真的回到今日清單（今天那筆在、昨天那筆不在）",
    (await row.count()) === 0 && (await todayRow.count()) === 1,
  );

  // 起日與迄日**都設成昨天**：昨天那筆要在、今天那筆要不在。
  // 只填起日的話，迄日壞掉（或整個沒送出）照樣會通過。
  // 營業日固定台北時區。用 DB session 時區算「昨天」的話，資料庫是 UTC 時，
  // 台北 00:00–07:59 跑這支會算成前一天而誤紅（Codex 第二輪）。
  const yesterday = psql(
    "SELECT ((now() AT TIME ZONE 'Asia/Taipei') - interval '1 day')::date",
  );
  await page.getByLabel("起日").fill(yesterday);
  await page.getByLabel("迄日").fill(yesterday);
  await page.getByRole("button", { name: "查詢" }).click();
  await row.first().waitFor({ timeout: 20000 });
  const rowText = (await row.first().textContent()) ?? "";
  ok(
    "日期範圍查得到昨天那筆，且不含今天那筆",
    rowText.includes("已作廢") && (await todayRow.count()) === 0,
    `${yesterday}~${yesterday}；${rowText.trim().slice(0, 36)}`,
  );
  await page.screenshot({ path: join(SHOTS, "04-by-date.png") });

  // 單號打成 #421054730 是店員最可能的輸入（畫面到處這樣顯示）——必須查得到。
  await page.getByRole("button", { name: "回今日" }).click();
  await todayRow.first().waitFor({ timeout: 20000 });
  await page.getByLabel("交易編號").fill(`#${sale.id}`);
  await page.getByRole("button", { name: "查詢" }).click();
  await row.first().waitFor({ timeout: 20000 });
  ok("交易編號帶 # 也查得到", true, `#${sale.id}`);
  await page.screenshot({ path: join(SHOTS, "05-by-hash-id.png") });
} catch (err) {
  ok(`未預期錯誤：${err.message}`, false);
} finally {
  await browser?.close();
}

const passed = results.filter((r) => r.pass).length;
console.log(`\n${passed}/${results.length} 通過`);
process.exit(passed === results.length ? 0 : 1);
