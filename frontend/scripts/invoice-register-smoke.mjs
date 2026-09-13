// 發票月報（報表頁「發票月報」分頁）瀏覽器煙霧：US-068 月底申報用的逐筆清單。
//
// **自己建前置資料**（真的開一張發票、再做一筆退貨），不是「找不到就當通過」——
// 那樣頁面壞掉、期間篩錯或分類錯了，煙霧照樣全綠。
// 驗：分頁進得去、合計與明細對得起來、期間篩選真的有作用、CSV 下載得到且內容含警示。
// 執行：node scripts/invoice-register-smoke.mjs（backend:8000 + frontend:3000 已起，docs/20）
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = strip(process.env.SMOKE_BASE ?? "http://localhost:3000");
const API_BASE = strip(process.env.SMOKE_API_BASE ?? "http://localhost:8000");
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "invoice-register");
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
function taipeiToday() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
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
  const runId = Date.now().toString().slice(-8);
  const { access_token: token } = await api("/api/v1/auth/login", {
    method: "POST",
    body: { username: USERNAME, password: PASSWORD },
  });
  if ((await api("/api/v1/cash-sessions/current", { token })) === null) {
    await api("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
      expect: [201],
    });
  }

  // 前置：收購一件 → 賣掉（本期一定有一筆交易可對）
  const seller = await api("/api/v1/contacts", {
    method: "POST",
    token,
    expect: [201],
    body: {
      name: `SMOKE_IR_${runId}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
    },
  });
  const acq = await api("/api/v1/acquisitions", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `SMOKE_IR_ACQ_${runId}` },
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [
        { name: `SMOKE_IR_ITEM_${runId}`, grade: "A", listed_price: "300", acquisition_cost: "100" },
      ],
    },
  });
  const sale = await api("/api/v1/sales", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `SMOKE_IR_SALE_${runId}` },
    body: {
      lines: [{ line_type: "SERIALIZED", item_code: acq.item_codes[0], qty: 1 }],
      tenders: [{ tender_type: "CASH", amount: "300" }],
    },
  });
  ok("前置：建立一筆本期交易", Boolean(sale.id), `#${sale.id}`);

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const pageErrors = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("帳號").fill(USERNAME);
  await page.getByLabel("密碼").fill(PASSWORD);
  await page.getByRole("button", { name: "登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

  await page.goto(`${BASE}/reports`, { waitUntil: "domcontentloaded" });
  await page.getByRole("tab", { name: "發票月報" }).click();
  // **只找合計卡片的標題**：說明文字裡也會出現「銷項合計」四個字，
  // 用全頁文字定位會一次命中三個元素而炸掉（Playwright strict mode）。
  const statLabel = (label) => page.locator(".rpt-stat dt", { hasText: label });
  await statLabel("銷項合計").first().waitFor({ timeout: 20000 });
  ok("報表頁進得去「發票月報」分頁", true);

  // 六個合計都要在（缺一個就代表匯出與畫面會對不起來）
  for (const label of ["銷項合計", "銷項稅額", "作廢合計", "折讓合計", "進項合計", "進項稅額"]) {
    await statLabel(label).first().waitFor({ timeout: 10000 });
  }
  ok("六個合計都顯示", true);

  // 每一段都要出現（沒有資料時顯示「本期間沒有…」，不能整段消失）
  const sections = [
    "銷項",
    "作廢",
    "折讓",
    "進項",
    "未完成",
    "前期發票本期作廢",
    "手開紙本待調整",
  ];
  const headings = await page.locator("h3.rpt-subtitle").allInnerTexts();
  const missing = sections.filter((s) => !headings.some((h) => h.startsWith(s)));
  ok("七個分段都在", missing.length === 0, missing.join("、") || headings.join(" / "));
  await page.screenshot({ path: join(SHOTS, "01-register.png"), fullPage: true });

  // 期間篩選要真的有作用：把起訖都設成很久以前 → 本期那些應該都不見
  const today = taipeiToday();
  await page.getByLabel("起始日期").fill("2020-01-01");
  await page.getByLabel("結束日期").fill("2020-01-31");
  await page.waitForTimeout(1500);
  const oldTotals = await page.locator(".rpt-stat").first().innerText();
  ok("改期間會重新查詢（舊期間合計為 0）", /(^|\D)0($|\D)/.test(oldTotals), oldTotals.replace(/\n/g, " "));
  await page.screenshot({ path: join(SHOTS, "02-other-period.png"), fullPage: true });

  await page.getByLabel("起始日期").fill(today);
  await page.getByLabel("結束日期").fill(today);
  await page.waitForTimeout(1500);

  // CSV 下載：會計拿到的是這個檔，內容要有店別/期間與類別欄
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 20000 }),
    page.getByRole("button", { name: "CSV" }).click(),
  ]);
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const csv = Buffer.concat(chunks).toString("utf8");
  ok(
    "CSV 下載得到且含店別、期間與類別欄",
    csv.includes("店別") && csv.includes("期間起") && csv.includes("類別"),
    csv.split("\n")[0].slice(0, 40),
  );
  ok(
    "CSV 的合計含稅額（會計要填的數字）",
    csv.includes("銷項稅額") && csv.includes("進項稅額"),
  );

  // 窄螢幕：表格在自己的框裡捲，整頁不橫捲
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  ok("窄螢幕整頁不橫向捲動", overflow <= 1, `溢出 ${overflow}px`);
  await page.screenshot({ path: join(SHOTS, "03-mobile.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (err) {
  ok(`未預期錯誤：${err.message}`, false);
} finally {
  await browser?.close();
  const passed = results.filter((r) => r.pass).length;
  console.log(`\n${passed}/${results.length} 通過`);
  console.log(`截圖：${SHOTS}`);
  if (passed !== results.length) process.exitCode = 1;
}
