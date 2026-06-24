// 實機列印示範（docs/21）：對「真 backend(:8000) + 真硬體代理(:8001, AGENT_DEVICES=real)」
//   1) 建立並啟用九折活動 → 收購自有序號品 → 折後結帳 → 取 SaleRead（含折扣留痕）
//      → POST 硬體代理 /print/detail → 印出「明細聯（顯示原價/折讓/活動名 + 折後總計）」。
//   2) 以假資料 InvoicePayload → POST /print/einvoice → 印出「測試電子發票證明聯」。
// 需先啟動：backend(:8000, seed dev-manager) 與 agent(:8001, AGENT_DEVICES=real,
//   AGENT_EPSON_HOST=<印表機IP>, AGENT_EINVOICE_AES_KEY=<32hex>)。
// 用法：API_BASE=http://localhost:8000 AGENT_BASE=http://localhost:8001 node scripts/print-discount-demo.mjs
import { randomUUID } from "node:crypto";
import { uniquePhone, validNationalId } from "./_national-id.mjs";

const API = (process.env.API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const AGENT = (process.env.AGENT_BASE ?? "http://localhost:8001").replace(/\/+$/, "");
const USER = process.env.SEED_USER ?? "dev-manager";
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";

async function call(base, path, { method = "GET", token, body, headers = {}, expected = [200, 201] } = {}) {
  const res = await fetch(`${base}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!expected.includes(res.status)) throw new Error(`${method} ${base}${path} → ${res.status}: ${text}`);
  return data;
}

const runId = `${Date.now()}-${randomUUID().slice(0, 6)}`;
const { access_token: token } = await call(API, "/api/v1/auth/login", {
  method: "POST",
  body: { username: USER, password: PASS },
});

if ((await call(API, "/api/v1/cash-sessions/current", { token })) === null) {
  await call(API, "/api/v1/cash-sessions/open", { method: "POST", token, body: { opening_float: "2000" } });
}

const now = new Date();
const camp = await call(API, "/api/v1/campaigns", {
  method: "POST",
  token,
  body: {
    name: "開幕九折",
    discount_pct: 10,
    starts_at: new Date(now.getTime() - 86400000).toISOString(),
    ends_at: new Date(now.getTime() + 86400000).toISOString(),
    applies_owned_serialized: true,
    applies_owned_bulk: true,
    applies_catalog: true,
    applies_consignment: false,
    consignment_discount_bearing: "STORE_ABSORBS",
  },
});
await call(API, `/api/v1/campaigns/${camp.id}/activate`, { method: "POST", token });

const seller = await call(API, "/api/v1/contacts", {
  method: "POST",
  token,
  expected: [201],
  body: { name: `列印示範賣方 ${runId}`, phone: uniquePhone(), national_id: validNationalId(), roles: ["SELLER"] },
});
const acq = await call(API, "/api/v1/acquisitions", {
  method: "POST",
  token,
  headers: { "Idempotency-Key": `prn-${runId}` },
  expected: [201],
  body: {
    type: "BUYOUT",
    contact_id: seller.id,
    payout_method: "CASH",
    items: [{ name: "活動帳篷", grade: "A", listed_price: "1000", acquisition_cost: "400" }],
  },
});
const code = acq.item_codes[0];

const sale = await call(API, "/api/v1/sales", {
  method: "POST",
  token,
  headers: { "Idempotency-Key": `prn-sale-${runId}` },
  expected: [200, 201],
  body: { lines: [{ line_type: "SERIALIZED", item_code: code }] },
});
console.log(`結帳完成：sale#${sale.id} 折後總計 ${sale.total}（折讓 ${sale.total_discount}）`);

// 1) 明細聯（顯示折扣）：把 SaleRead 直接送代理；補上活動名（代理印、不算）。
await call(AGENT, "/print/detail", {
  method: "POST",
  expected: [200],
  body: { ...sale, campaign_name: camp.name },
});
console.log("✅ 已送印：商品明細聯（含原價/折讓/活動名 + 折後總計）");

// 2) 測試電子發票證明聯（假資料）。
await call(AGENT, "/print/einvoice", {
  method: "POST",
  expected: [200],
  body: {
    sale_id: sale.id,
    invoice_number: "AA12345678",
    invoice_date: now.toISOString().slice(0, 10),
    invoice_time: "12:00:00",
    random_code: "1234",
    sales_amount: sale.subtotal,
    tax_amount: sale.tax,
    total_amount: sale.total,
    seller_tax_id: "00000000",
    seller_name: "露坑（測試）",
    lines: sale.lines.map((l) => ({
      line_type: l.line_type,
      description: l.description,
      qty: l.qty,
      unit_price: l.unit_price,
      line_total: l.line_total,
    })),
  },
});
console.log("✅ 已送印：測試電子發票證明聯（假資料）");
console.log("\n完成。請查看 EPSON 出紙：明細聯應顯示『原價1000 折-100 / 活動折扣 -100 / 開幕九折 / 總計 900』。");
