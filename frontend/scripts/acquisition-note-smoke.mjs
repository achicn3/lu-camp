// 收購逐列備註煙霧：一列填備註 + N 件 → 送出 → **真的 N 件各自都帶著那則備註**。
//
// 裁示（2026-09-04）：一列一則備註，套用該列全部件數。畫面上填一次，展開成 N 筆
// 各自獨立的商品時每件都要拿到；要分別註記就拆成多列填。
//
// 沿用 acquisition-qty-smoke 的標準：**真相在資料庫，不在畫面**。填完欄位截圖證明不了
// 備註有沒有真的落到那 N 件上，送出後一律回頭問 API。
//
// 執行（backend:8000 + frontend:3000 已起、已開帳）：
//   node frontend/scripts/acquisition-note-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "acq-note");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const RUN = String(Date.now()).slice(-6);
const ITEM_A = `備註帳篷-${RUN}`;
const ITEM_B = `備註爐頭-${RUN}`;
const NOTE_A = "缺營釘一支，交貨前要跟客人說";
const NOTE_B = "附原廠盒，盒角有壓痕";
const QTY_A = 3;
const COST = 500;

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
const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

async function fillRow(row, { name, price, cost, category }) {
  await row.getByLabel("品名").fill(name);
  await row.locator("select").first().selectOption("A");
  const cat = row.getByLabel("分類");
  await cat.click();
  await cat.fill(category);
  // 第一次要建分類，之後同名直接選既有的。
  const create = page.locator(`button:has-text("建立「${category}」")`);
  if (await create.count()) await create.first().click();
  else await page.locator(`button:has-text("${category}")`).first().click();
  await row.getByLabel("上架售價（含稅）").fill(String(price));
  await row.getByLabel("收購價").fill(String(cost));
}

try {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

  await page.goto(`${BASE}/acquisition`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "收購" }).first().waitFor({ timeout: 15000 });

  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', `備註賣家-${RUN}`);
  await page.fill('input[aria-label="手機"]', uniquePhone(RUN));
  await page.fill('input[aria-label="身分證字號"]', validNationalId(RUN));
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector(`text=備註賣家-${RUN}`, { timeout: 15000 });

  const CATEGORY = `備註分類-${RUN}`;
  const rows = page.locator(".acq-row");

  // 第一列：3 件 + 備註 A
  await fillRow(rows.nth(0), { name: ITEM_A, price: 1500, cost: COST, category: CATEGORY });
  await rows.nth(0).getByLabel("商品備註").fill(NOTE_A);
  await rows.nth(0).getByLabel("件數").fill(String(QTY_A));
  await page.waitForTimeout(400);

  // 多件時要明說這則備註會套用到全部幾件——否則店員不知道自己填的影響範圍。
  const rowText = await rows.nth(0).innerText();
  ok(`多件時提示「套用到本列全部 ${QTY_A} 件」`,
    rowText.includes(`套用到本列全部 ${QTY_A} 件`),
    rowText.match(/這則備註[^\n]*/)?.[0] ?? "（找不到提示）");
  await page.screenshot({ path: join(SHOTS, "01-acq-note-row.png"), fullPage: true });

  // 第二列：1 件 + 不同備註 B（驗證兩列互不污染）
  await page.click('button:has-text("＋ 新增一列")');
  await fillRow(rows.nth(1), { name: ITEM_B, price: 900, cost: 300, category: CATEGORY });
  await rows.nth(1).getByLabel("商品備註").fill(NOTE_B);
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(SHOTS, "02-two-rows.png"), fullPage: true });

  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 20000 });
  await page.screenshot({ path: join(SHOTS, "03-submitted.png") });

  // ── 真相在資料庫 ──
  const fetchItems = async (name) => {
    const data = await apiJson(`/api/v1/serialized-items?q=${encodeURIComponent(name)}&limit=50`, { token });
    return (Array.isArray(data) ? data : data.items ?? []).filter((i) => i.name === name);
  };

  const a = await fetchItems(ITEM_A);
  ok(`第一列建出 ${QTY_A} 件`, a.length === QTY_A, `實際 ${a.length} 件`);
  ok(`${QTY_A} 件**每一件**都帶著同一則備註`,
    a.length === QTY_A && a.every((i) => i.note === NOTE_A),
    a.map((i) => `${i.item_code}=${i.note ?? "（無）"}`).join(" | "));

  const b = await fetchItems(ITEM_B);
  ok("第二列建出 1 件且帶自己的備註（兩列不互相污染）",
    b.length === 1 && b[0].note === NOTE_B,
    b.map((i) => `${i.item_code}=${i.note ?? "（無）"}`).join(" | "));

  // 展開後各自獨立：改其中一件不影響其他件。
  if (a.length === QTY_A) {
    await apiJson(`/api/v1/serialized-items/${a[0].id}/note`, {
      method: "PATCH", token, body: { note: "只有這一件後來發現有刮痕" },
    });
    const after = await fetchItems(ITEM_A);
    const changed = after.filter((i) => i.note === "只有這一件後來發現有刮痕");
    const untouched = after.filter((i) => i.note === NOTE_A);
    ok("改其中一件的備註不影響同列其他件（展開後各自獨立）",
      changed.length === 1 && untouched.length === QTY_A - 1,
      `已改 ${changed.length} 件、未動 ${untouched.length} 件`);
  }

  // 庫存頁列表看得到摘要（店員不必逐件點開）
  await page.goto(`${BASE}/inventory`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[role="tab"]:has-text("序號品")', { timeout: 15000 });
  await page.locator('input[placeholder*="品名"]').first().fill(ITEM_A);
  await page.click('button:has-text("查詢")');
  await page.waitForTimeout(1200);
  const summaries = await page.locator(".inv-note-summary").count();
  ok("庫存列表顯示備註摘要", summaries >= QTY_A - 1, `實得 ${summaries} 筆`);
  await page.screenshot({ path: join(SHOTS, "04-inventory-list.png"), fullPage: true });

} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.p);
console.log(`\n${results.length - failed.length}/${results.length} 通過；截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
