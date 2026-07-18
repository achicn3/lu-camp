// LINE Pay 端到端真沙盒驗收（docs/30 §8.5，P2e）：解真 oneTimeKey → 經真 /sales API 真收費
// → 驗 payment_method/手續費/非現金不進抽屜 → 作廢觸發真退款。全程對真沙盒＋真 backend＋真 DB。
// 執行：node scripts/linepay-acceptance.mjs（需 backend:8000 已帶 LINEPAY_* env、DB=lucamp_e2e）。
import { execSync } from "node:child_process";

import jsQR from "jsqr";
import { chromium } from "playwright";
import { PNG } from "pngjs";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const API = "http://localhost:8000";
const SANDBOX = "https://sandbox-web-pay.line.me/web/sandbox/payment/oneTimeKey?countryCode=TW";
const results = [];
const ok = (name, pass, detail = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
};

async function api(path, { method = "GET", token, body, headers = {}, expect = [200] } = {}) {
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
  const data = text ? JSON.parse(text) : null;
  if (!expect.includes(res.status)) {
    throw new Error(`${method} ${path} → ${res.status}: ${text}`);
  }
  return { status: res.status, data };
}

function psql(sql) {
  const out = execSync(
    `docker exec lu-camp-db-1 psql -U lucamp -d lucamp_e2e -tAc "${sql}"`,
    { encoding: "utf8" },
  );
  return out.trim();
}

async function decodeOneTimeKey() {
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto(SANDBOX, { waitUntil: "networkidle", timeout: 30000 });
  await p.waitForTimeout(1200);
  const src = await p.evaluate(() => document.querySelectorAll("img")[0]?.src || "");
  await b.close();
  const png = PNG.sync.read(Buffer.from(src.split(",")[1], "base64"));
  const qr = jsQR(new Uint8ClampedArray(png.data), png.width, png.height);
  if (!qr) throw new Error("QR 解碼失敗");
  return qr.data.trim();
}

try {
  const oneTimeKey = await decodeOneTimeKey();
  ok("解碼真 oneTimeKey", true, oneTimeKey);

  const { data: login } = await api("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  const token = login.access_token;

  // 啟用 LINE Pay、費率 1.5%
  await api("/api/v1/settings", {
    method: "PATCH",
    token,
    body: { linepay_enabled: true, linepay_fee_pct: "0.0150" },
  });

  // 開帳（收購付現需要）→ 建賣方 → 收購一件序號品
  const cur = await api("/api/v1/cash-sessions/current", { token });
  if (cur.data === null) {
    await api("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
      expect: [201],
    });
  }
  const runId = `${Date.now()}`;
  const { data: seller } = await api("/api/v1/contacts", {
    method: "POST",
    token,
    expect: [201],
    body: {
      name: `LP驗收賣方${runId}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
      member_points: 0,
      source_note: "linepay acceptance",
    },
  });
  const { data: acq } = await api("/api/v1/acquisitions", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `acq-${runId}` },
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      note: "LP驗收",
      items: [{ name: "LP驗收帳篷", grade: "A", listed_price: "300", acquisition_cost: "120" }],
    },
  });
  const code = acq.item_codes[0];

  // 折後總額
  const { data: quote } = await api("/api/v1/sales/quote", {
    method: "POST",
    token,
    body: { lines: [{ line_type: "SERIALIZED", item_code: code, qty: 1 }] },
  });
  const total = String(quote.total);
  const expectedFee = String(Math.round(Number(total) * 0.015));

  // ★ 真收費：LINE_PAY tender + 真 oneTimeKey。201 即證明平台真的回 0000 扣款成功（否則 fail-closed 402）。
  const idem = `lp-accept-${runId}`;
  const { status: saleStatus, data: sale } = await api("/api/v1/sales", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": idem },
    body: {
      lines: [{ line_type: "SERIALIZED", item_code: code, qty: 1 }],
      tenders: [{ tender_type: "LINE_PAY", amount: total, line_pay_one_time_key: oneTimeKey }],
    },
  });
  ok("真收費：POST /sales 回 201（平台真回 0000 扣款）", saleStatus === 201, `#${sale.id} 總額 ${total}`);
  ok("payment_method = LINE_PAY", sale.payment_method === "LINE_PAY", sale.payment_method);
  const tender = (sale.tenders || []).find((t) => t.tender_type === "LINE_PAY");
  ok(
    `tender fee_amount = ${expectedFee}（1.5%）`,
    tender?.amount === total && tender?.fee_amount === expectedFee,
    tender ? `amount=${tender.amount} fee=${tender.fee_amount}` : "無",
  );

  // DB：linepay_transactions COMPLETE + 交易號（19 位字串無失真）
  const txnRow = psql(
    `SELECT status || '|' || transaction_id || '|' || amount FROM linepay_transactions WHERE sale_id=${sale.id}`,
  );
  const [txStatus, txId, txAmount] = txnRow.split("|");
  ok(
    "DB linepay_transactions = COMPLETE + 真交易號",
    txStatus === "COMPLETE" && /^\d{15,}$/.test(txId) && txAmount === total,
    txnRow,
  );

  // 非現金、不進抽屜：此筆銷售無 cash_movement
  const cashMoves = psql(
    `SELECT COUNT(*) FROM cash_movements WHERE ref_type='sale' AND ref_id=${sale.id}`,
  );
  ok("非現金不進抽屜（此銷售無 cash_movement）", cashMoves === "0", `count=${cashMoves}`);

  // 財報不 break：日結現金報表可取（LINE Pay 非現金，不灌現金收入）
  const today = new Date().toISOString().slice(0, 10);
  const { status: dcStatus, data: dc } = await api(
    `/api/v1/reports/daily-cash?date=${today}`,
    { token, expect: [200] },
  );
  // LINE Pay 非現金：不灌現金銷售收入（sales_cash_in 不含這 270）。報表可取即證整合不 break。
  ok("日結現金報表可取（LINE Pay 非現金，整合不 break）", dcStatus === 200, JSON.stringify(dc?.totals ?? {}).slice(0, 80));

  // ★ 真退款：作廢 → 觸發 refund。200 即證明平台真的退款成功（否則 fail-closed 402）。
  const { status: voidStatus, data: voided } = await api(`/api/v1/sales/${sale.id}/void`, {
    method: "POST",
    token,
    expect: [200],
  });
  ok("真退款：作廢回 200（平台真退款成功）", voidStatus === 200, voided.invoice_status);
  const afterVoid = psql(
    `SELECT status || '|' || refunded_amount FROM linepay_transactions WHERE sale_id=${sale.id}`,
  );
  ok(
    "DB linepay_transactions = REFUNDED + 全額退",
    afterVoid === `REFUNDED|${total}`,
    afterVoid,
  );

  const failed = results.filter((r) => !r.pass);
  console.log(`\n${failed.length === 0 ? "✅ 全數通過" : `❌ ${failed.length} 失敗`}（${results.length} 檢查）`);
  process.exit(failed.length === 0 ? 0 : 1);
} catch (err) {
  console.error("驗收中止：", err);
  process.exit(1);
}
