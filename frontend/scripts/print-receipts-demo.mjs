// 實機列印示範（真實資料）：所有金額一律由後端結帳引擎計算（不手刻 payload），
// 印幾張「正常」（無活動）＋幾張「折扣」（九折）明細聯到 EPSON，並印出 SaleRead 數字供核對。
// 需先啟動 backend(:8000, 已 seed dev-manager + seed_dev_purchasing) 與 agent(:8001, AGENT_DEVICES=real)。
// 用法：API_BASE=http://localhost:8000 AGENT_BASE=http://localhost:8001 node scripts/print-receipts-demo.mjs
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

const token = (await call(API, "/api/v1/auth/login", { method: "POST", body: { username: USER, password: PASS } }))
  .access_token;
if ((await call(API, "/api/v1/cash-sessions/current", { token })) === null) {
  await call(API, "/api/v1/cash-sessions/open", { method: "POST", token, body: { opening_float: "5000" } });
}

// 結束任何生效中活動，確保「正常」批次真的無折扣。
for (const c of await call(API, "/api/v1/campaigns?status=ACTIVE", { token })) {
  await call(API, `/api/v1/campaigns/${c.id}/end`, { method: "POST", token });
}

const catalog = await call(API, "/api/v1/catalog-products?limit=200", { token });
const bySku = Object.fromEntries(catalog.map((p) => [p.sku, p]));
const lantern = bySku["LANTERN-USB"]; // 售價 590、現量 15
const peg = bySku["PEG-Y"]; // 售價 180、現量 8
if (!lantern || !peg) throw new Error("找不到種子商品；請先跑 seed_dev_purchasing");

async function makeSerialized(listed) {
  const runId = randomUUID().slice(0, 8);
  const seller = await call(API, "/api/v1/contacts", {
    method: "POST",
    token,
    expected: [201],
    body: { name: `示範賣方 ${runId}`, phone: uniquePhone(), national_id: validNationalId(), roles: ["SELLER"] },
  });
  const acq = await call(API, "/api/v1/acquisitions", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": `acq-${runId}` },
    expected: [201],
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [{ name: "示範帳篷", grade: "A", listed_price: String(listed), acquisition_cost: "400" }],
    },
  });
  return acq.item_codes[0];
}

async function checkoutAndPrint(label, lines, campaignName) {
  const sale = await call(API, "/api/v1/sales", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": `sale-${randomUUID().slice(0, 8)}` },
    expected: [200, 201],
    body: { lines },
  });
  // 後端算好的數字（核對用）：Σ line_total = total；total_discount = Σ 各行 discount_amount。
  const sumLines = sale.lines.reduce((a, l) => a + Number(l.line_total), 0);
  const sumDisc = sale.lines.reduce((a, l) => a + Number(l.discount_amount), 0);
  console.log(`\n【${label}】sale#${sale.id}`);
  for (const l of sale.lines) {
    console.log(
      `  ${l.description} x${l.qty} 單價${l.unit_price} 小計${l.line_total}` +
        (Number(l.discount_amount) ? ` (原價${l.original_unit_price} 折-${l.discount_amount})` : ""),
    );
  }
  console.log(`  總計=${sale.total} 未稅=${sale.subtotal} 稅=${sale.tax} 折讓總額=${sale.total_discount}`);
  console.log(
    `  核對：Σ小計=${sumLines}${sumLines === Number(sale.total) ? "=總計✓" : "≠總計✗"}；` +
      `未稅+稅=${Number(sale.subtotal) + Number(sale.tax)}${Number(sale.subtotal) + Number(sale.tax) === Number(sale.total) ? "=總計✓" : "✗"}；` +
      `Σ折讓=${sumDisc}${sumDisc === Number(sale.total_discount) ? "=折讓總額✓" : "≠折讓總額✗"}`,
  );
  try {
    await call(AGENT, "/print/detail", {
      method: "POST",
      expected: [200],
      body: { ...sale, campaign_name: campaignName ?? null },
    });
    console.log(`  ✅ 已送印 EPSON`);
  } catch (e) {
    console.log(`  ⚠️ 送印失敗（不影響金額核對）：${String(e).split("\n")[0]}`);
  }
}

// ── 正常（無活動）──
await checkoutAndPrint("正常①：營燈x2 + 營釘x1", [
  { line_type: "CATALOG", catalog_product_id: lantern.id, qty: 2 },
  { line_type: "CATALOG", catalog_product_id: peg.id, qty: 1 },
]);
await checkoutAndPrint("正常②：序號帳篷（標價1000）", [
  { line_type: "SERIALIZED", item_code: await makeSerialized(1000) },
]);

// ── 折扣（九折，套用序號 + 散裝 + catalog）──
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

await checkoutAndPrint(
  "折扣①：營燈x12 + 營釘x3（驗 qty>1 整行折讓）",
  [
    { line_type: "CATALOG", catalog_product_id: lantern.id, qty: 12 },
    { line_type: "CATALOG", catalog_product_id: peg.id, qty: 3 },
  ],
  camp.name,
);
await checkoutAndPrint(
  "折扣②：序號帳篷（標價1000→900）",
  [{ line_type: "SERIALIZED", item_code: await makeSerialized(1000) }],
  camp.name,
);

console.log("\n完成：請核對 EPSON 出紙——正常張無折讓列；折扣張每行『原價x數量 折-整行折讓』、底部『活動折扣 -Σ』、總計=未稅+稅。");
