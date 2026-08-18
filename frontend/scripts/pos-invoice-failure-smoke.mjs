// POS 開立失敗 → 重試 → 改手開紙本的瀏覽器煙霧（docs/36）。
//
// **必須用真的 Amego 測試環境**：這段畫面的三個分支（開立中／開立失敗＋重試／已登記手開紙本）
// 只有在真的送出並被平台拒絕時才會出現。用假資料蓋出來的狀態驗不到重試按鈕與錯誤訊息。
//
// 前置（見 docs/20、docs/24）：
//   - backend(:8000) 啟動時帶 `AMEGO_APP_KEY`（值在 repo 根目錄 .env，已被 .gitignore 排除）
//   - 店家統編**故意不是** Amego 測試統編 12345678 → 平台拒絕 → 穩定重現「開立失敗」
//     （用正確統編會真的開出測試發票，就驗不到失敗分支了）
//   - `einvoice_enabled = true`
// 執行：node scripts/pos-invoice-failure-smoke.mjs
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "pos-invoice-failure");
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

const RUN = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
const INVOICE_NO = `ZX${RUN.slice(-8)}`;

const login = await api(null, "POST", "/api/v1/auth/login", {
  username: USERNAME,
  password: PASSWORD,
});
if (login.status !== 200) {
  ok("API 登入", false, `HTTP ${login.status}`);
  process.exit(1);
}
const token = login.json.access_token;

// 前置：電子發票必須開著，否則結帳根本不會建 PENDING 發票、也不會呼叫開立。
const settings = await api(token, "PATCH", "/api/v1/settings", { einvoice_enabled: true });
ok(
  "啟用電子發票（前置）",
  settings.status === 200,
  settings.status === 200 ? "" : JSON.stringify(settings.json),
);
if (settings.status !== 200) process.exit(1);

const store = await api(token, "GET", "/api/v1/stores/1/receipt-header");
const taxId = store.json?.tax_id ?? null;
// **判準寫死在腳本裡**：用測試統編會真的開出發票，本腳本要驗的失敗分支就不會出現，
// 而且會在 Amego 測試後台留下一堆垃圾發票。與其之後困惑，不如當場擋下。
// **查不到統編就直接失敗**：若把 null 當成「不是 12345678」而放行，這道防呆等於不存在
// （實測踩過：打錯端點回 404 → taxId 為空 → 斷言假綠）。
ok(
  "店家統編不是 Amego 測試統編（否則會真的開出發票，驗不到失敗分支）",
  typeof taxId === "string" && /^[0-9]{8}$/.test(taxId) && taxId !== "12345678",
  `tax_id=${taxId ?? "(查不到)"}`,
);
if (!(typeof taxId === "string" && /^[0-9]{8}$/.test(taxId) && taxId !== "12345678")) {
  process.exit(1);
}

const cash = await api(token, "GET", "/api/v1/cash-sessions/current");
if (cash.json === null || cash.json?.id === undefined) {
  const opened = await api(token, "POST", "/api/v1/cash-sessions/open", { opening_float: "2000" });
  ok("開帳（前置）", opened.status < 400, `HTTP ${opened.status}`);
} else {
  ok("開帳（前置）", true, "已開帳");
}

const product = await api(token, "POST", "/api/v1/catalog-products", {
  sku: `POSFAIL-${RUN}`,
  name: `開立失敗測試品-${RUN}`,
  unit_price: "100",
});
ok("上架商品（前置）", product.status === 201, `HTTP ${product.status}`);
const supplier = await api(token, "POST", "/api/v1/suppliers", { name: `失敗測試供應商-${RUN}` });
const po = await api(token, "POST", "/api/v1/purchase-orders", {
  supplier_id: supplier.json.id,
  submit: true,
  lines: [{ catalog_product_id: product.json.id, qty: 2, unit_cost: "50" }],
});
await api(
  token,
  "POST",
  `/api/v1/purchase-orders/${po.json.id}/receive`,
  { lines: po.json.lines.map((l) => ({ line_id: l.id, qty: l.qty })) },
  { "Idempotency-Key": `posfail-recv-${randomUUID()}` },
);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector("text=掃描或輸入商品條碼");
  await page.fill('input[name="code"]', `POSFAIL-${RUN}`);
  await page.press('input[name="code"]', "Enter");
  await page.waitForSelector(`text=開立失敗測試品-${RUN}`);
  await page.click('button:has-text("結帳")');
  await page.waitForSelector("text=已完成");
  // 完成後會跳「列印商品明細」對話框並蓋住底下的發票提示區，先關掉。
  const printDialog = page.locator('[role="dialog"][aria-label="列印商品明細"]');
  if (await printDialog.isVisible().catch(() => false)) {
    await printDialog.getByRole("button", { name: /不用，完成|^完成$/ }).click();
    await printDialog.waitFor({ state: "hidden" });
  }

  // ── 1) 平台拒絕：交易照樣成立，畫面明說「發票尚未開立」且給得出重試入口 ──
  const note = page.locator(".pos-invoice-note");
  await note.waitFor();
  const retry = page.locator("button.pos-invoice-retry");
  await retry.waitFor({ timeout: 20000 });
  const failText = (await note.textContent()) ?? "";
  ok("開立失敗但交易已成立（畫面明說）", failText.includes("發票尚未開立"), failText.slice(0, 120));
  ok("失敗時提供「重試開立」入口", await retry.isVisible());
  // 只丟平台錯誤碼，店員不知道下一步能做什麼——字軌用完/平台故障正是改開紙本的時機。
  ok(
    "失敗訊息要說得出下一步（改開紙本並登記）",
    failText.includes("紙本") && failText.includes("交易紀錄"),
    failText.slice(0, 160),
  );
  await page.screenshot({ path: `${SHOTS}/01-issue-failed.png` });

  // 銷售必須真的存在且為未開立——不能因為發票失敗就把交易吞掉。
  const sales = await api(token, "GET", "/api/v1/sales?limit=5");
  const sale = (sales.json ?? [])[0];
  ok(
    "銷售已寫入且為未開立",
    sale != null && sale.invoice_status === "PENDING_ISSUE",
    `#${sale?.id} ${sale?.invoice_status}`,
  );

  // ── 2) 再按一次重試：平台仍拒絕，狀態不得被洗成「已開立」 ──
  await retry.click();
  await page.waitForTimeout(3000);
  const secondText = (await note.textContent()) ?? "";
  ok("重試仍失敗時不得謊稱已開立", secondText.includes("發票尚未開立"), secondText.slice(0, 120));
  ok("重試失敗後仍留著重試入口", await retry.isVisible());
  await page.screenshot({ path: `${SHOTS}/02-retry-failed.png` });

  // ── 3) 另一台終端登記了手開紙本 → 這台按重試必須改口，不可導引去光貿補印 ──
  // 這正是 docs/36 的主場景：字軌用完/平台故障時當場開紙本給客人。
  const reg = await api(token, "POST", `/api/v1/einvoice/sales/${sale.id}/manual-invoice`, {
    invoice_no: INVOICE_NO,
    invoice_date: new Date().toISOString().slice(0, 10),
    total: String(sale.total),
    note: "字軌用完（POS 失敗煙霧）",
  });
  ok("另一台終端登記手開紙本（前置）", reg.status === 200, `HTTP ${reg.status}`);

  await retry.click();
  await page.waitForTimeout(3000);
  const manualText = (await note.textContent()) ?? "";
  ok(
    "改口為「已登記手開紙本發票」",
    manualText.includes("手開紙本發票") && manualText.includes(INVOICE_NO),
    manualText.slice(0, 160),
  );
  // 平台上沒有這張、也沒有條碼：導引去光貿後台補印只會讓店員撲空。
  ok(
    "不得導引去光貿後台補印（平台上沒有這張）",
    !manualText.includes("invoice.amego.tw") && !manualText.includes("補印"),
    manualText.slice(0, 160),
  );
  ok(
    "不提供列印電子證明聯",
    (await page.locator("button.pos-invoice-reprint").count()) === 0,
  );
  ok("成功後不再顯示重試入口", (await page.locator("button.pos-invoice-retry").count()) === 0);
  await page.screenshot({ path: `${SHOTS}/03-manual-paper-registered.png` });

  // 來源欄位只在**清單**（SaleSummaryRead）上，明細 schema 沒有——查明細會拿到 undefined
  // 而把斷言弄成假綠。
  const afterList = await api(token, "GET", "/api/v1/sales?limit=5");
  const after = (afterList.json ?? []).find((r) => r.id === sale.id);
  ok(
    "銷售轉為已開立且來源為手開紙本",
    after?.invoice_status === "ISSUED" && after?.invoice_issue_channel === "MANUAL_PAPER",
    `${after?.invoice_status}/${after?.invoice_issue_channel}`,
  );
} catch (err) {
  ok("煙霧流程例外", false, String(err));
  await page.screenshot({ path: `${SHOTS}/99-error.png` }).catch(() => {});
} finally {
  await browser.close();
}

// **還原店家設定**：留著會讓後續腳本的結帳被「須宣告發票設定狀態」擋成 409。
await api(token, "PATCH", "/api/v1/settings", { einvoice_enabled: false });

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
