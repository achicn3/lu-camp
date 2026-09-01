// 收購同款多件煙霧：一列填 N 件 → 送出 → **真的建出 N 件獨立商品**。
//
// 這支刻意做完整流程（建賣方 → 填鑑價 → 送出 → 回頭查 API）。先前只填欄位＋截圖的版本
// 證明不了「送出後真的是 N 件」，而 Codex 對抗式審查抓到的兩個 High 正好都藏在送出這一段：
//   1. 件數不合法時 validateDraft 沒擋，那一列被靜默丟掉（店員以為收了、實際沒建）
//   2. 買斷填了件數再切到寄售，件數欄隱藏但值還在，寄售一列會變成 N 件
// 只驗畫面的煙霧對這兩件事完全無感。
//
// 執行（backend:8000 + frontend:3000 已起、已開帳）：
//   node frontend/scripts/acquisition-qty-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "acq-qty");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const RUN = String(Date.now()).slice(-6);
const ITEM_NAME = `多件帳篷-${RUN}`;
const COST = 500;
const QTY = 3;

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
  method: "POST",
  body: { username: USERNAME, password: PASSWORD },
});

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

  await page.goto(`${BASE}/acquisition`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "收購" }).first().waitFor({ timeout: 15000 });

  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', `多件賣家-${RUN}`);
  await page.fill('input[aria-label="手機"]', uniquePhone(RUN));
  await page.fill('input[aria-label="身分證字號"]', validNationalId(RUN));
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector(`text=多件賣家-${RUN}`, { timeout: 15000 });

  await page.fill('input[aria-label="品名"]', ITEM_NAME);
  await page.locator(".acq-row select").first().selectOption("A");
  const cat = page.getByLabel("分類");
  await cat.click();
  await cat.fill(`多件分類-${RUN}`);
  await page.click(`button:has-text("建立「多件分類-${RUN}」")`);
  await page.fill('input[aria-label="上架售價（含稅）"]', "1500");
  await page.fill('input[aria-label="收購價"]', String(COST));

  const qtyBox = page.getByLabel("件數").first();
  ok("件數欄預設為 1", (await qtyBox.inputValue()) === "1", await qtyBox.inputValue());

  // 件數不合法必須擋住送出，而不只是顯示紅字（Codex High #1）。
  //
  // **必須用「一列合法＋一列非法」**：只有一列且件數 0 時，展開結果是空陣列，後端
  // 本來就會拒收空品項——那樣即使前端完全沒有守衛，畫面也不會出現「收購完成」，
  // 測試會為了錯的理由變綠（Codex 第二輪指出）。混合列才驗得出前端有沒有真的擋。
  await page.click('button:has-text("＋ 新增一列")');
  const rows = page.locator(".acq-row");
  await rows.nth(1).getByLabel("品名").fill(`${ITEM_NAME}-B`);
  await rows.nth(1).locator("select").first().selectOption("A");
  await rows.nth(1).getByLabel("上架售價（含稅）").fill("1500");
  await rows.nth(1).getByLabel("收購價").fill(String(COST));
  await rows.nth(1).getByLabel("件數").fill("0");
  await page.waitForTimeout(400);

  let posted = false;
  const watch = (req) => {
    if (req.method() === "POST" && req.url().includes("/api/v1/acquisitions")) posted = true;
  };
  page.on("request", watch);
  await page.click('button:has-text("送出收購")');
  await page.waitForTimeout(2000);
  page.off("request", watch);
  ok("一列合法＋一列件數 0：整單被擋，連 POST 都沒發出", !posted && (await page.locator("text=收購完成").count()) === 0);

  await rows.nth(1).locator('button:has-text("移除")').click();
  await page.waitForTimeout(300);
  await qtyBox.fill(String(QTY));
  await page.waitForTimeout(400);
  const bodyText = await page.locator("body").innerText();
  ok(`合計顯示 ${COST * QTY}`,
    bodyText.includes(`此列共 ${QTY} 件`) && bodyText.replace(/,/g, "").includes(String(COST * QTY)),
    bodyText.match(/此列共[^\n]*/)?.[0] ?? "（找不到）");
  await page.screenshot({ path: join(SHOTS, "01-qty-row.png") });

  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 20000 });
  await page.screenshot({ path: join(SHOTS, "02-submitted.png") });

  // 真相在資料庫，不在畫面：回頭問 API 到底建了幾件、付了多少
  const items = await apiJson(`/api/v1/serialized-items?q=${encodeURIComponent(ITEM_NAME)}&limit=50`, { token });
  const mine = (Array.isArray(items) ? items : items.items ?? []).filter((i) => i.name === ITEM_NAME);
  ok(`資料庫真的建出 ${QTY} 件`, mine.length === QTY, `實際 ${mine.length} 件`);
  ok("每件各有不同的序號條碼（可分別上架/售出）",
    new Set(mine.map((i) => i.item_code)).size === mine.length,
    mine.map((i) => i.item_code).join(", "));
  ok("三件內容完全一致（同款多件，不是三筆各自不同的商品）",
    new Set(mine.map((i) => `${i.grade}|${i.listed_price}|${i.category_id}`)).size === 1,
    mine.map((i) => `${i.grade}/${i.listed_price}`).join(", "));

  // 最關鍵的一項：**付給客人的錢**必須是單價 × 件數。收購價填的是每件，若被當成
  // 三件的總價，客人就被少付 1000 元。畫面只顯示單號，總額要回頭問 API。
  const doneText = await page.locator("body").innerText();
  const acqId = doneText.match(/單號\s*#(\d+)/)?.[1];
  ok("完成畫面帶出收購單號", Boolean(acqId), doneText.match(/收購完成[^\n]*/)?.[0] ?? "");
  if (acqId) {
    const acq = await apiJson(`/api/v1/acquisitions/${acqId}`, { token });
    ok(`實付客人 ${COST * QTY} 元（單價 × 件數）`,
      acq.total_cash_paid === String(COST * QTY), String(acq.total_cash_paid));
    // 順帶確認金額沒有以科學記號回傳（本分支另一項修正）。
    ok("金額為純十進位、非科學記號",
      !/[eE]/.test(String(acq.total_cash_paid)), String(acq.total_cash_paid));
  }

} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.p);
console.log(`\n${results.length - failed.length}/${results.length} 通過；截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
