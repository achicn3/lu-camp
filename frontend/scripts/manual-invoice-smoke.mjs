// docs/36 手開紙本發票登記 瀏覽器煙霧測試。
//
// 需 backend(:8000) + frontend(:3000) 已起，且**啟用電子發票**（否則銷售不會建 PENDING 發票）。
// 前置資料（開帳、商品、一筆待開立的銷售）由本腳本自行以 API 補齊。
// 執行：SMOKE_BASE=http://localhost:3000 node scripts/manual-invoice-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "manual-invoice");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const RUN = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
// 每輪換號碼：同店發票號碼唯一，寫死會在第二輪撞既有登記。
const INVOICE_NO = `ZZ${RUN.slice(-8)}`;

async function api(token, method, path, body, extraHeaders = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...extraHeaders,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

const login = await api(null, "POST", "/api/v1/auth/login", {
  username: "dev-manager",
  password: "dev-test-123456",
});
if (login.status !== 200) {
  ok("API 登入", false, `HTTP ${login.status}`);
  process.exit(1);
}
const token = login.json.access_token;

// 電子發票必須啟用，否則銷售不建 PENDING 發票、也就沒有「未開立」可登記。
const settings = await api(token, "PATCH", "/api/v1/settings", { einvoice_enabled: true });
if (settings.status >= 400) {
  ok("啟用電子發票（前置）", false, `HTTP ${settings.status}：${JSON.stringify(settings.json)}`);
  process.exit(1);
}
ok("啟用電子發票（前置）", true);

const session = await api(token, "GET", "/api/v1/cash-sessions/current");
if (session.json === null || session.status === 404) {
  const opened = await api(token, "POST", "/api/v1/cash-sessions/open", {
    opening_float: "1000",
  });
  ok("開帳（前置）", opened.status < 400, `HTTP ${opened.status}`);
} else {
  ok("開帳（前置）", true, "已開帳");
}

// 一般商品 → 建一筆現金銷售（發票會停在待開立：本機沒有可用的 Amego 憑證）
const product = await api(
  token,
  "POST",
  "/api/v1/catalog-products",
  { name: `手開測試品-${RUN}`, unit_price: "500", reorder_point: 0 },
  { "Idempotency-Key": `manual-inv-prod-${RUN}` },
);
ok("上架商品（前置）", product.status < 400, `HTTP ${product.status}`);
const supplier = await api(token, "POST", "/api/v1/suppliers", { name: `手開測試商-${RUN}` });
const po = await api(token, "POST", "/api/v1/purchase-orders", {
  supplier_id: supplier.json.id,
  submit: true,
  lines: [{ catalog_product_id: product.json.id, qty: 3, unit_cost: "300" }],
});
await api(
  token,
  "POST",
  `/api/v1/purchase-orders/${po.json.id}/receive`,
  { lines: po.json.lines.map((l) => ({ line_id: l.id, qty: l.qty })) },
  { "Idempotency-Key": `manual-inv-recv-${RUN}` },
);
const sale = await api(
  token,
  "POST",
  "/api/v1/sales",
  {
    lines: [{ line_type: "CATALOG", catalog_product_id: product.json.id, qty: 1 }],
    tenders: [{ tender_type: "CASH", amount: "500" }],
    expected_einvoice_enabled: true,
  },
  { "Idempotency-Key": `manual-inv-sale-${RUN}` },
);
ok("建立待開立發票的銷售（前置）", sale.status === 201, `HTTP ${sale.status}`);
const saleId = sale.json?.id;

// 第二筆：登記完第一筆之後，只有這筆仍是「未開立」。有兩筆才分辨得出篩選到底有沒有生效
// ——只有一筆時，篩不篩都是 1 筆，測試會**假綠**（本腳本先前就是這樣漏掉前端沒接上）。
const sale2 = await api(
  token,
  "POST",
  "/api/v1/sales",
  {
    lines: [{ line_type: "CATALOG", catalog_product_id: product.json.id, qty: 1 }],
    tenders: [{ tender_type: "CASH", amount: "500" }],
    expected_einvoice_enabled: true,
  },
  { "Idempotency-Key": `manual-inv-sale2-${RUN}` },
);
ok("建立第二筆待開立銷售（前置）", sale2.status === 201, `HTTP ${sale2.status}`);
const saleId2 = sale2.json?.id;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.waitForSelector("table tbody tr");

  // ── 只看未開立：把離開 POS 後找不到的單撈回來 ──
  // 先在未篩選狀態登記第一筆，之後才有「一筆已開立、一筆未開立」可分辨。
  const openRegister = async (id) => {
    await page.locator(`button[aria-label="登記銷售 ${id} 的手開發票"]`).click();
    const d = page.locator('[role="dialog"][aria-label="登記手開發票"]');
    await d.waitFor();
    return d;
  };
  const unfilteredCount = await page.locator("table tbody tr").count();
  ok("未篩選時兩筆都在", unfilteredCount >= 2, `${unfilteredCount} 筆`);

  // ── 登記手開發票 ──
  const dialog = await openRegister(saleId);
  await page.screenshot({ path: `${SHOTS}/02-register-dialog.png` });

  // 格式錯誤要當場擋下，不是送出去才 422
  await dialog.getByLabel("發票號碼").fill("BAD123");
  await page.waitForTimeout(300);
  ok(
    "號碼格式錯誤即時擋下",
    (await dialog.locator("text=格式須為").count()) > 0 &&
      (await dialog.getByRole("button", { name: "確認登記" }).isDisabled()),
  );
  await page.screenshot({ path: `${SHOTS}/03-invalid-number-blocked.png` });

  await dialog.getByLabel("發票號碼").fill(INVOICE_NO);
  await dialog.getByLabel("隨機碼").fill("1234");
  await dialog.getByLabel("事由").fill("字軌用完");
  await page.waitForTimeout(300);
  await dialog.getByRole("button", { name: "確認登記" }).click();
  await page.waitForSelector("text=已登記手開發票", { timeout: 20000 });
  ok("登記成功", true, INVOICE_NO);
  await page.screenshot({ path: `${SHOTS}/04-registered.png` });

  // ── 登記後：該筆不再可登記 ──
  await page.waitForTimeout(1200);
  const stillPending =
    (await page.locator(`button[aria-label="登記銷售 ${saleId} 的手開發票"]`).count()) > 0;
  ok("登記後不再可登記手開", !stillPending);

  // ── 篩選必須真的改變集合（否則就是前端沒接上後端，會假綠）──
  const filter = page.getByLabel(/只看未開立發票的交易/);
  await filter.check();
  await page.waitForTimeout(1200);
  const filteredIds = await page.locator("table tbody tr td:nth-child(2)").allTextContents();
  ok(
    "篩選後只剩仍未開立的那筆",
    filteredIds.length === 1 && filteredIds[0].includes(String(saleId2)),
    filteredIds.join(","),
  );
  await filter.uncheck();
  await page.waitForTimeout(1200);
  const backCount = await page.locator("table tbody tr").count();
  ok("取消篩選後兩筆都回來", backCount >= 2, `${backCount} 筆`);
  await page.screenshot({ path: `${SHOTS}/05-after-register.png` });

  // ── 登記後按作廢：必須**當場**說紙本程序，絕不可先叫店員去退款 ──
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector("table tbody tr");
  await page.locator(`button[aria-label="作廢銷售 ${saleId}"]`).click();
  const voidDialog = page.locator('[role="dialog"][aria-label="作廢銷售確認"]');
  await voidDialog.waitFor();
  const voidText = (await voidDialog.textContent()) ?? "";
  ok(
    "作廢對話框直接說紙本程序",
    voidText.includes("手開紙本發票") && voidText.includes("國稅局"),
  );
  ok(
    "不會先要求店員去退款（台灣Pay 指示/確認框都不該出現）",
    !voidText.includes("手動退款") && !voidText.includes("確認作廢"),
    voidText.includes("手動退款") ? "仍出現退款指示" : "",
  );
  await page.screenshot({ path: `${SHOTS}/06-void-blocked.png` });
  await voidDialog.getByRole("button", { name: "知道了" }).click();

  // ── 後端事實查核：來源、佇列取消 ──
  const invoice = await api(token, "GET", `/api/v1/sales/${saleId}`);
  ok(
    "銷售的發票狀態轉為已開立",
    invoice.json?.invoice_status === "ISSUED",
    String(invoice.json?.invoice_status),
  );
  // 現在有兩筆：登記過的那筆必須從待送佇列消失，**未登記的第二筆本來就該還在**。
  // 只斷言「完全沒有待送」會誤判——那反而代表把別人的佇列也取消了。
  const queue = await api(token, "GET", "/api/v1/einvoice/queue?status=PENDING&limit=200");
  const pending = (queue.json?.items ?? []).filter((i) => i.invoice_id != null);
  ok(
    "登記過的那筆已離開待送佇列，未登記的那筆仍在",
    pending.length === 1,
    `待送 ${pending.length} 筆`,
  );
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
