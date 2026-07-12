// 電子發票（Amego 光貿，docs/24）POS 煙霧：
// A) B2C 一般結帳 → 自動開立（真打 Amego 測試環境）→ 證明聯送印（agent fake，驗 payload 帶平台條碼/QR 內容）
// B) 手機載具 → 開立但不印證明聯（無 /print/einvoice 請求）
// C) B2B（統編）→ 開立（B2B 分稅）→ 證明聯送印（payload 帶買方統編）
// 需 backend:8000（AMEGO_APP_KEY 已設、店家 tax_id 已填、einvoice_enabled=true）、
// frontend:3000、agent:8001（fake）。每次執行會在 Amego 測試後台留下真發票（test@amego.tw 可查）。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "einvoice");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(method, path, token, body, extraHeaders = {}) {
  const r = await fetch(`${API}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...extraHeaders,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await r.json();
  } catch {
    // 空回應
  }
  return { status: r.status, data };
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 950 } });
page.on("pageerror", (e) => ok("頁面 JS 錯誤", false, String(e)));
// 追蹤送往 agent 的證明聯列印請求（載具流程須「沒有」）。
const einvoicePrints = [];
page.on("request", (req) => {
  if (req.url().includes("/print/einvoice")) einvoicePrints.push(req.postDataJSON());
});

try {
  const login = await apiJson("POST", "/api/v1/auth/login", null, {
    username: "dev-manager",
    password: "dev-test-123456",
  });
  const mgr = login.data.access_token;
  await apiJson("POST", "/api/v1/cash-sessions/open", mgr, { opening_float: "1000" });

  // 備貨：收購三件可售品（現金撥款）。
  const contact = await apiJson("POST", "/api/v1/contacts", mgr, {
    name: "發票測試賣家",
    phone: `09${Date.now().toString().slice(-8)}`,
    national_id: "B100000002",
    roles: ["SELLER", "MEMBER"],
  });
  const acq = await apiJson(
    "POST",
    "/api/v1/acquisitions",
    mgr,
    {
      type: "BUYOUT",
      contact_id: contact.data.id,
      items: [1, 2, 3, 4].map((n) => ({
        name: `發票測試品${n}`,
        grade: "A",
        listed_price: "500",
        acquisition_cost: "100",
      })),
      payout_method: "CASH",
    },
    { "Idempotency-Key": `einv-acq-${Date.now()}` },
  );
  ok("備貨（收購三件）", acq.status === 201, `status=${acq.status}`);
  const [item1, item2, item3, item4] = acq.data.item_codes;

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector(".pos-invoice");
  ok("POS 顯示電子發票輸入區（統編/載具/捐贈碼）", true);
  await page.screenshot({ path: join(SHOTS, "01-invoice-fields.png"), fullPage: true });

  // ── A) B2C 一般：自動開立＋證明聯送印 ─────────────────────────────
  await page.fill(".pos-scan-input", item1);
  await page.press(".pos-scan-input", "Enter");
  await page.waitForSelector("text=發票測試品1");
  const printResp = page.waitForResponse(
    (r) => r.url().includes("/print/einvoice") && r.status() === 200,
    { timeout: 20000 },
  );
  await page.click(".pos-checkout");
  await page.waitForSelector("text=已完成", { timeout: 15000 });
  await printResp;
  await page.waitForSelector("text=證明聯已送印", { timeout: 10000 });
  const noteA = await page.textContent(".pos-invoice-note");
  ok("B2C 結帳自動開立（真 Amego 測試環境）", /發票 [A-Z]{2}\d{8}/.test(noteA ?? ""), noteA ?? "");
  const printA = einvoicePrints.at(-1);
  ok(
    "證明聯 payload 帶平台條碼/QR 內容（不本地推算）",
    printA?.barcode_content?.length >= 19 &&
      !!printA?.qrcode_left_content &&
      !!printA?.qrcode_right_content &&
      printA?.buyer_tax_id == null,
    `barcode=${printA?.barcode_content}`,
  );
  // 重印證明聯（Codex 第十六輪）：完成畫面常駐重印鈕，按下再送一次列印。
  // 先關自動彈出的明細列印對話框（overlay 會攔截點擊）。
  await page.click('button:has-text("不用，完成")');
  const reprintResp = page.waitForResponse(
    (r) => r.url().includes("/print/einvoice") && r.status() === 200,
    { timeout: 15000 },
  );
  await page.click('button:has-text("重印證明聯")');
  await reprintResp;
  ok("完成畫面可重印證明聯", true);
  await page.screenshot({ path: join(SHOTS, "02-b2c-issued.png"), fullPage: true });

  // ── B) 手機載具：開立但不印 ───────────────────────────────────────
  await page.click('button:has-text("開始下一筆")');
  await page.fill(".pos-scan-input", item2);
  await page.press(".pos-scan-input", "Enter");
  await page.waitForSelector("text=發票測試品2");
  await page.fill('input[name="inv-carrier"]', "/TRM+O+P");
  // 載具已填 → 統編/捐贈碼應被鎖住（互斥）
  ok(
    "統編/捐贈碼與載具互斥（已鎖）",
    (await page.locator('input[name="inv-tax-id"]').isDisabled()) &&
      (await page.locator('input[name="inv-npoban"]').isDisabled()),
  );
  const printsBefore = einvoicePrints.length;
  await page.click(".pos-checkout");
  await page.waitForSelector("text=已完成", { timeout: 15000 });
  await page.waitForSelector("text=存入載具", { timeout: 15000 });
  const noteB = await page.textContent(".pos-invoice-note");
  ok("載具結帳：開立且不印證明聯", /發票 [A-Z]{2}\d{8}/.test(noteB ?? "") && noteB.includes("不印"), noteB ?? "");
  await page.waitForTimeout(1200);
  ok("載具流程沒有送出列印請求", einvoicePrints.length === printsBefore);
  await page.screenshot({ path: join(SHOTS, "03-carrier.png"), fullPage: true });

  // ── C) B2B（統編）：開立＋證明聯送印（帶買方統編）──────────────────
  await page.click('button:has-text("不用，完成")');
  await page.click('button:has-text("開始下一筆")');
  await page.fill(".pos-scan-input", item3);
  await page.press(".pos-scan-input", "Enter");
  await page.waitForSelector("text=發票測試品3");
  await page.fill('input[name="inv-tax-id"]', "22099131");
  await page.fill('input[name="inv-buyer-name"]', "台灣積體電路製造股份有限公司");
  const printRespC = page.waitForResponse(
    (r) => r.url().includes("/print/einvoice") && r.status() === 200,
    { timeout: 20000 },
  );
  await page.click(".pos-checkout");
  await page.waitForSelector("text=已完成", { timeout: 15000 });
  await printRespC;
  await page.waitForSelector("text=證明聯已送印", { timeout: 10000 });
  const noteC = await page.textContent(".pos-invoice-note");
  const printC = einvoicePrints.at(-1);
  ok("B2B 結帳開立＋送印", /發票 [A-Z]{2}\d{8}/.test(noteC ?? ""), noteC ?? "");
  ok("B2B 證明聯帶買方統編", printC?.buyer_tax_id === "22099131", `buyer=${printC?.buyer_tax_id}`);
  await page.screenshot({ path: join(SHOTS, "04-b2b-issued.png"), fullPage: true });
  // ── D) fail-closed：settings 讀取失敗 → 結帳鈕停用（Codex 第十九輪）──────
  // 降級的 settings 不得讓結帳以 invoice:null 開出預設 B2C（丟失統編/載具選擇）。
  const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 950 } });
  const page2 = await ctx2.newPage();
  await page2.route("**/api/v1/settings", (route) => route.abort());
  await page2.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page2.waitForTimeout(400);
  await page2.fill('input[name="username"]', "dev-manager");
  await page2.fill('input[name="password"]', "dev-test-123456");
  await page2.click('button:has-text("登入")');
  await page2.waitForURL(`${BASE}/`);
  const salesRequests = [];
  page2.on("request", (req) => {
    // 只認結帳本體（排除 /sales/quote 試算）
    if (/\/api\/v1\/sales$/.test(new URL(req.url()).pathname) && req.method() === "POST")
      salesRequests.push(req);
  });
  await page2.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page2.waitForSelector("text=無法讀取發票設定");
  // 掃**未售**品成立有效購物車（其他結帳前置全綠），唯獨 settings 失敗 → 仍不可結帳。
  await page2.fill(".pos-scan-input", item4);
  await page2.press(".pos-scan-input", "Enter");
  await page2.waitForSelector("text=發票測試品4");
  await page2.waitForTimeout(800); // 等試算完成（quote 與 settings 無關）
  ok(
    "settings 失敗＋有效購物車：結帳鈕仍停用（fail-closed）",
    await page2.locator(".pos-checkout").isDisabled(),
  );
  await page2.locator(".pos-checkout").click({ force: true }).catch(() => {});
  await page2.waitForTimeout(500);
  ok("settings 失敗時零 /sales 請求", salesRequests.length === 0);
  await page2.screenshot({ path: join(SHOTS, "05-settings-failclosed.png"), fullPage: true });
  await ctx2.close();
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
