// 餐飲內用/外帶報表瀏覽器煙霧（docs/39）：真 backend＋真 Postgres＋真瀏覽器。
// 重點不只是「畫面出得來」，而是**口徑正確**：
//   - 佔比的分母是「有餐飲的單」——加入純二手單後佔比不得改變
//   - 客單價只算餐飲行——同單的二手成交不得灌水
//   - 口徑說明必須顯示在畫面上（不能只寫在文件裡）
// 需 backend(:8000) + frontend(:3000) 已起。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "dine-in-report");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body, extraHeaders = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
      ...extraHeaders,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: res.status, json: await res.json().catch(() => null) };
}

const RUN = Date.now().toString().slice(-6);
const login = await api(null, "POST", "/api/v1/auth/login", {
  username: USERNAME,
  password: PASSWORD,
});
if (login.status !== 200) {
  ok("API 登入", false, `HTTP ${login.status}`);
  process.exit(1);
}
const token = login.json.access_token;

// 前置：開帳、桌號、餐飲品項、一般商品
const cash = await api(token, "GET", "/api/v1/cash-sessions/current");
if (cash.json === null || cash.json?.id === undefined) {
  await api(token, "POST", "/api/v1/cash-sessions/open", { opening_float: "2000" });
}
await api(token, "PATCH", "/api/v1/settings", { dine_in_tables: ["A1", "A2"] });
// **如實宣告發票開關**：本店若已啟用，結帳未宣告會被擋成 409。
// 不在此翻動全店設定——那會污染其他腳本（本專案已踩過）。
const settings = await api(token, "GET", "/api/v1/settings");
const einvoiceEnabled = settings.json?.einvoice_enabled === true;
const menu = await api(token, "POST", "/api/v1/menu-items", {
  name: `煙霧咖啡-${RUN}`,
  unit_price: "180",
});
ok("建立餐飲品項（前置）", menu.status === 201, `HTTP ${menu.status}`);

const product = await api(token, "POST", "/api/v1/catalog-products", {
  sku: `DIR-${RUN}`,
  name: `煙霧裝備-${RUN}`,
  unit_price: "500",
});
const supplier = await api(token, "POST", "/api/v1/suppliers", { name: `DIR供應商-${RUN}` });
const po = await api(token, "POST", "/api/v1/purchase-orders", {
  supplier_id: supplier.json.id,
  submit: true,
  lines: [{ catalog_product_id: product.json.id, qty: 20, unit_cost: "200" }],
});
await api(
  token,
  "POST",
  `/api/v1/purchase-orders/${po.json.id}/receive`,
  { lines: po.json.lines.map((l) => ({ line_id: l.id, qty: l.qty })) },
  { "Idempotency-Key": `dir-recv-${RUN}` },
);

async function menuSale({ mode, withProduct = false, n }) {
  const lines = [{ line_type: "MENU", menu_item_id: menu.json.id, qty: 1 }];
  let total = 180;
  if (withProduct) {
    lines.push({ line_type: "CATALOG", catalog_product_id: product.json.id, qty: 1 });
    total += 500;
  }
  return api(
    token,
    "POST",
    "/api/v1/sales",
    {
      lines,
      tenders: [{ tender_type: "CASH", amount: String(total) }],
      service_mode: mode,
      expected_einvoice_enabled: einvoiceEnabled,
      ...(mode === "DINE_IN" ? { table_no: "A1" } : {}),
    },
    { "Idempotency-Key": `dir-${mode}-${n}-${RUN}` },
  );
}

// 內用 2 組（其中一組併買裝備）、外帶 2 組 → 佔比應為 50/50
const created = [
  await menuSale({ mode: "DINE_IN", n: 1 }),
  await menuSale({ mode: "DINE_IN", n: 2, withProduct: true }),
  await menuSale({ mode: "TAKEOUT", n: 3 }),
  await menuSale({ mode: "TAKEOUT", n: 4 }),
];
// **每一筆都要檢查**：不檢查的話四筆全失敗仍會一路往下跑，直到數字是 0 才露餡。
const bad = created.filter((r) => r.status !== 201);
ok(
  "四筆餐飲交易都建立成功（前置）",
  bad.length === 0,
  bad.map((r) => `HTTP ${r.status} ${JSON.stringify(r.json?.detail ?? "")}`).join(" | "),
);
if (bad.length > 0) process.exit(1);

const today = new Date();
const from = new Date(today.getTime() - 864e5).toISOString();
const to = new Date(today.getTime() + 864e5).toISOString();
const before = await api(token, "GET", `/api/v1/reports/dine-in?from=${from}&to=${to}`);
ok("報表端點可用", before.status === 200, `HTTP ${before.status}`);
const shareBefore = before.json?.summary?.dine_in?.share;

// **口徑紅線**：加入純二手單後，內用佔比不得改變
for (let i = 0; i < 5; i += 1) {
  await api(
    token,
    "POST",
    "/api/v1/sales",
    {
      lines: [{ line_type: "CATALOG", catalog_product_id: product.json.id, qty: 1 }],
      tenders: [{ tender_type: "CASH", amount: "500" }],
      expected_einvoice_enabled: einvoiceEnabled,
    },
    { "Idempotency-Key": `dir-prod-${i}-${RUN}` },
  );
}
const after = await api(token, "GET", `/api/v1/reports/dine-in?from=${from}&to=${to}`);
ok(
  "加入純二手單後，內用佔比不變（分母是「有餐飲的單」）",
  after.json?.summary?.dine_in?.share === shareBefore,
  `${shareBefore} → ${after.json?.summary?.dine_in?.share}`,
);

// **客單價只算餐飲行**：內用有一組併買了 $500 裝備，客單價仍應是 180
ok(
  "客單價只算餐飲行（同單的二手成交不灌水）",
  after.json?.summary?.dine_in?.avg_ticket === "180",
  `客單價=${after.json?.summary?.dine_in?.avg_ticket}、整單合計=${after.json?.summary?.dine_in?.gross_total}`,
);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

// **攔截畫面自己收到的回應**再比對，才是真的「數字與後端一致」。
// 拿另一個日期區間查來的結果去比，比的是兩個不同的東西（本腳本第一版就這樣錯過）。
let rendered = null;
page.on("response", async (resp) => {
  if (resp.url().includes("/api/v1/reports/dine-in") && resp.request().method() === "GET") {
    try {
      rendered = await resp.json();
    } catch {
      /* 匯出等非 JSON 回應略過 */
    }
  }
});

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "餐飲內用/外帶" }).click();
  await page.waitForSelector("table.rpt-dine-in-summary", { timeout: 20000 });
  ok("報表分頁載入", true);

  // 口徑說明**必須顯示在畫面上**，不能只寫在文件裡
  const basis = (await page.locator(".rpt-dine-in-basis").allTextContents()).join(" ");
  ok(
    "畫面上寫明分母是「有餐飲的單」",
    basis.includes("有餐飲的單"),
    basis.slice(0, 80),
  );
  ok("畫面上寫明客單價只計餐飲品項", basis.includes("只計餐飲品項"));
  ok(
    "畫面上警告內用與外帶客單價不可直接比較",
    basis.includes("不可直接比較"),
  );

  const summaryText = (await page.locator("table.rpt-dine-in-summary").textContent()) ?? "";
  ok("摘要顯示內用與外帶", summaryText.includes("內用") && summaryText.includes("外帶"));

  // **畫面數字必須與後端一致**（docs/39 §7）：只檢查「有出現內用/外帶這幾個字」
  // 不會發現前端把欄位接錯（例如把整單合計當成客單價顯示）。
  ok("已攔截到畫面收到的報表回應", rendered !== null);
  const expected = rendered.summary;
  const dineRow = page.locator("table.rpt-dine-in-summary tbody tr").first();
  const cells = (await dineRow.locator("td").allTextContents()).map((t) => t.trim());
  const [, groupsCell, shareCell, revenueCell, avgCell, grossCell] = cells;
  ok(
    "內用組數與後端一致",
    groupsCell === String(expected.dine_in.groups),
    `畫面=${groupsCell} 後端=${expected.dine_in.groups}`,
  );
  ok(
    "內用佔比與後端一致（換算成百分比）",
    shareCell === `${(Number(expected.dine_in.share) * 100).toFixed(1)}%`,
    `畫面=${shareCell} 後端=${expected.dine_in.share}`,
  );
  ok(
    "餐飲營收與客單價各就各位（沒有把整單合計當客單價）",
    revenueCell === `$${expected.dine_in.fnb_revenue}` &&
      avgCell === `$${expected.dine_in.avg_ticket}` &&
      grossCell === `$${expected.dine_in.gross_total}`,
    `營收=${revenueCell} 客單價=${avgCell} 整單=${grossCell}`,
  );
  await page.screenshot({ path: `${SHOTS}/01-summary.png` });

  await page.waitForSelector("table.rpt-dine-in-hourly");
  ok("時段分佈表出現", true);
  await page.screenshot({ path: `${SHOTS}/02-trend-hourly.png`, fullPage: true });

  // 匯出：規格明寫要有，而且**檔案要帶著口徑**（離開系統後畫面上的提醒跟不過去）
  ok("匯出鈕存在", (await page.locator(".rpt-download-bar button").count()) >= 2);
  const csv = await api(
    token,
    "GET",
    `/api/v1/reports/dine-in?from=${from}&to=${to}&format=csv`,
  );
  ok("CSV 匯出可用", csv.status === 200 || csv.status === undefined);

  // 切每週不得爆掉
  await page.selectOption(".rpt-filters select", "week");
  await page.waitForTimeout(1500);
  ok(
    "切換每週後仍正常",
    (await page.locator("table.rpt-dine-in-summary").count()) === 1,
  );
} catch (err) {
  ok("煙霧流程例外", false, String(err));
  await page.screenshot({ path: `${SHOTS}/99-error.png` }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
