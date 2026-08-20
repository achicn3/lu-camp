// LINE Pay 真沙盒交易灌檔（docs/30）：解真 oneTimeKey → 經真 /sales API 真收費 → 部分真退款。
//
// **oneTimeKey 只能從沙盒頁的條碼圖解出來**，沒有 API 可以產生——這是 Offline v4
// 「店家掃客人碼」的本質（真店面是用掃碼槍讀客人手機）。所以每一筆都要重載一次沙盒頁、
// 解一次 QR，速度受此限制，數量自然落在數十筆而不是上千筆。
//
// 前置：backend 帶 LINEPAY_* env 指向 lucamp_manual（見 docs/37）。
//
//   node scripts/seed-linepay.mjs --count 24 --refunds 6

import { chromium } from "playwright";
import jsQR from "jsqr";
import { PNG } from "pngjs";

const API = process.env.LINEPAY_SEED_API ?? "http://127.0.0.1:8010";
const SANDBOX = "https://sandbox-web-pay.line.me/web/sandbox/payment/oneTimeKey?countryCode=TW";
const argOf = (name, dflt) => {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? Number(process.argv[i + 1]) : dflt;
};
const COUNT = argOf("count", 24);
const REFUNDS = argOf("refunds", 6);

async function api(path, { method = "GET", token, body, headers = {} } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  return { status: res.status, data };
}

// 瀏覽器只開一次、每筆重載頁面取新碼：每次重開瀏覽器要多花好幾秒。
async function makeKeyReader() {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  return {
    async next() {
      await page.goto(SANDBOX, { waitUntil: "networkidle", timeout: 45000 });
      await page.waitForTimeout(1200);
      const src = await page.evaluate(() => document.querySelectorAll("img")[0]?.src || "");
      if (!src.includes(",")) throw new Error("沙盒頁沒有條碼圖");
      const png = PNG.sync.read(Buffer.from(src.split(",")[1], "base64"));
      const qr = jsQR(new Uint8ClampedArray(png.data), png.width, png.height);
      if (!qr) throw new Error("QR 解碼失敗");
      return qr.data.trim();
    },
    close: () => browser.close(),
  };
}

const { data: login } = await api("/api/v1/auth/login", {
  method: "POST",
  body: { username: "dev-manager", password: process.env.SEED_USER_PASSWORD ?? "devpass1234" },
});
const token = login?.access_token;
if (!token) throw new Error("登入失敗：請確認 backend 指向 lucamp_manual");

// 退貨退款必須在開帳中的班別下進行（§7 不變量 #8）——與折讓腳本同一個道理。
const cur = await api("/api/v1/cash-sessions/current", { token });
let openedHere = false;
if (cur.status !== 200 || !cur.data?.id) {
  const opened = await api("/api/v1/cash-sessions/open", {
    method: "POST", token, body: { opening_float: "30000" },
  });
  if (opened.status >= 300) throw new Error(`開帳失敗 ${opened.status}`);
  openedHere = true;
}

const reader = await makeKeyReader();
const paid = [];
let failed = 0;

try {
  for (let i = 0; i < COUNT; i += 1) {
    const inv = await api("/api/v1/serialized-items?status=IN_STOCK&limit=40", { token });
    // 回應是純陣列，不是 {items:[...]}
    const rows = Array.isArray(inv.data) ? inv.data : (inv.data?.items ?? []);
    const pool = rows.filter((it) => Number(it.listed_price) > 0);
    const item = pool[Math.floor(Math.random() * pool.length)];
    if (!item) { console.log("沒有可售庫存，提前結束"); break; }

    let oneTimeKey;
    try { oneTimeKey = await reader.next(); }
    catch (e) { console.log(`  取碼失敗：${e.message}`); failed += 1; continue; }

    const total = String(item.listed_price);
    const res = await api("/api/v1/sales", {
      method: "POST",
      token,
      headers: { "Idempotency-Key": `seed-linepay-${Date.now()}-${i}` },
      body: {
        lines: [{ line_type: "SERIALIZED", item_code: item.item_code }],
        tenders: [{ tender_type: "LINE_PAY", amount: total, line_pay_one_time_key: oneTimeKey }],
        // 電子發票已啟用時，HTTP 客戶端**必須**宣告自己觀察到的設定值，
        // 否則 409——避免版本落後的收銀端靜默開出預設 B2C、漏收統編/載具/捐贈。
        expected_einvoice_enabled: true,
      },
    });
    if (res.status === 201) {
      paid.push({ saleId: res.data.id, total });
      console.log(`✅ ${paid.length}/${COUNT} 真收費 ${total} 元（sale #${res.data.id}）`);
    } else {
      failed += 1;
      console.log(`❌ 收費失敗 ${res.status}：${JSON.stringify(res.data).slice(0, 140)}`);
    }
  }
} finally {
  await reader.close();
}

// 部分真退款：退貨會呼叫 LINE Pay refund，linepay_refund_attempts 才有資料。
let refunded = 0;
for (const sale of paid.slice(0, REFUNDS)) {
  const detail = await api(`/api/v1/sales/${sale.saleId}`, { token });
  const line = detail.data?.lines?.[0];
  if (!line) continue;
  const res = await api("/api/v1/returns", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": `seed-linepay-refund-${sale.saleId}` },
    body: {
      sale_id: sale.saleId,
      reason: "客人現場改變主意",
      lines: [{ sale_line_id: line.id, qty: 1 }],
      invoice_recalled: true,
    },
  });
  if (res.status === 201) { refunded += 1; console.log(`↩️  真退款 sale #${sale.saleId}`); }
  else console.log(`   退款未成立 ${res.status}：${JSON.stringify(res.data).slice(0, 140)}`);
}

if (openedHere) {
  const s = await api("/api/v1/cash-sessions/current", { token });
  if (s.data?.id) {
    await api(`/api/v1/cash-sessions/${s.data.id}/close`, {
      method: "POST", token, body: { counted_amount: String(s.data.expected_amount ?? "30000") },
    });
  }
}

console.log(`\n真收費 ${paid.length} 筆／真退款 ${refunded} 筆／失敗 ${failed} 筆`);
