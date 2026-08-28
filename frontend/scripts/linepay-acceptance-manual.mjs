// LINE Pay sandbox acceptance against the isolated lucamp_manual database.
// This evidence script deliberately never prints or persists the one-time payment key.
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";

import jsQR from "jsqr";
import { chromium } from "playwright";
import { PNG } from "pngjs";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { taipeiDateForScript } from "./_taipei-date.mjs";

const API = "http://127.0.0.1:8000";
const SANDBOX = "https://sandbox-web-pay.line.me/web/sandbox/payment/oneTimeKey?countryCode=TW";
const DB = process.env.QA_DB || "lucamp_manual";
const OUT =
  process.env.QA_OUT ||
  "/home/test/tmp/lu-camp-manual-20260826/qa-data/linepay-acceptance.json";

if (!/^lucamp_(manual|e2e)$/.test(DB)) {
  throw new Error(`Refusing to run LINE Pay acceptance against non-QA database: ${DB}`);
}

const results = [];
const evidence = { database: DB, sandbox: true, created_data: {}, checks: results };
function check(name, pass, detail = {}) {
  results.push({ name, pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}`);
}

async function api(path, { method = "GET", token, body, headers = {}, expect = [200] } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const raw = await response.text();
  const data = raw ? JSON.parse(raw) : null;
  if (!expect.includes(response.status)) {
    throw new Error(`${method} ${path} returned unexpected HTTP ${response.status}`);
  }
  return { status: response.status, data };
}

function psql(sql) {
  return execFileSync(
    "docker",
    ["exec", "lu-camp-db-1", "psql", "-U", "lucamp", "-d", DB, "-tAc", sql],
    { encoding: "utf8" },
  ).trim();
}

async function decodeOneTimeKey() {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.goto(SANDBOX, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(1200);
    const source = await page.evaluate(() => document.querySelector("img")?.src || "");
    const png = PNG.sync.read(Buffer.from(source.split(",")[1], "base64"));
    const qr = jsQR(new Uint8ClampedArray(png.data), png.width, png.height);
    if (!qr) throw new Error("LINE Pay sandbox QR decode failed");
    return qr.data.trim();
  } finally {
    await browser.close();
  }
}

async function main() {
  let token;
  let settingsWasEnabled = false;
  let originalFeePct = null;
  try {
    const { data: login } = await api("/api/v1/auth/login", {
      method: "POST",
      body: { username: "dev-manager", password: "dev-test-123456" },
    });
    token = login.access_token;
    const { data: originalSettings } = await api("/api/v1/settings", { token });
    settingsWasEnabled = Boolean(originalSettings.linepay_enabled);
    // **費率也要記下來還原**：本腳本會把它改成 1.5%，只還原開關的話，QA 庫的費率會被
    // 永久改掉；而且原本就啟用時舊版連還原都不做（Codex 第二輪）。
    originalFeePct = originalSettings.linepay_fee_pct ?? null;

    await api("/api/v1/settings", {
      method: "PATCH",
      token,
      body: { linepay_enabled: true, linepay_fee_pct: "0.0150" },
    });

    const oneTimeKey = await decodeOneTimeKey();
    check("LP01 decoded a real sandbox one-time key without persisting it", oneTimeKey.length > 10);

    const current = await api("/api/v1/cash-sessions/current", { token });
    if (current.data === null) {
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
        name: `CODEX_TEST_LINEPAY_${runId}`,
        phone: uniquePhone(),
        national_id: validNationalId(),
        roles: ["SELLER"],
        member_points: 0,
        source_note: "CODEX_TEST LINE Pay sandbox acceptance",
      },
    });
    evidence.created_data.contact_id = seller.id;

    const { data: acquisition } = await api("/api/v1/acquisitions", {
      method: "POST",
      token,
      expect: [201],
      headers: { "Idempotency-Key": `CODEX_TEST_LP_ACQ_${runId}` },
      body: {
        type: "BUYOUT",
        contact_id: seller.id,
        payout_method: "CASH",
        note: "CODEX_TEST LINE Pay sandbox acceptance",
        items: [
          {
            name: `CODEX_TEST_LINEPAY_ITEM_${runId}`,
            grade: "A",
            listed_price: "300",
            acquisition_cost: "120",
          },
        ],
      },
    });
    evidence.created_data.acquisition_id = acquisition.acquisition_id;
    evidence.created_data.item_code = acquisition.item_codes[0];

    const { data: quote } = await api("/api/v1/sales/quote", {
      method: "POST",
      token,
      body: {
        lines: [{ line_type: "SERIALIZED", item_code: acquisition.item_codes[0], qty: 1 }],
      },
    });
    const total = String(quote.total);
    const expectedFee = String(Math.round(Number(total) * 0.015));
    const idempotencyKey = `CODEX_TEST_LP_SALE_${runId}`;

    // LINE Pay 收款必須綁在「客顯已配對」的權威購物車上（sales/service.py:898）——
    // 否則有人能拿別台 POS 購物車的一次性付款碼扣款。取現有的有效配對，
    // 把同一份明細與付款推上客顯購物車，拿回 cart_session_id / revision。
    const terminalId = Number(
      psql(
        "SELECT pos_terminal_id FROM terminal_kiosk_pairings WHERE unpaired_at IS NULL ORDER BY id DESC LIMIT 1",
      ),
    );
    if (!terminalId) throw new Error("找不到有效的客顯配對，無法驗 LINE Pay（請先在系統中配對客顯）");
    const { data: cart } = await api(`/api/v1/customer-display/terminals/${terminalId}/cart`, {
      method: "PUT",
      token,
      expect: [200, 201],
      body: {
        lines: [{ line_type: "SERIALIZED", item_code: acquisition.item_codes[0], qty: 1 }],
        tenders: [{ tender_type: "LINE_PAY", amount: total }],
      },
    });
    evidence.created_data.cart_session_id = cart.id;
    check("LP01b 客顯權威購物車已建立並配對", Boolean(cart.id) && cart.revision >= 1, {
      terminal_id: terminalId,
      cart_session_id: cart.id,
      revision: cart.revision,
    });

    const saleBody = {
      lines: [{ line_type: "SERIALIZED", item_code: acquisition.item_codes[0], qty: 1 }],
      tenders: [
        { tender_type: "LINE_PAY", amount: total, line_pay_one_time_key: oneTimeKey },
      ],
      cart_session_id: cart.id,
      cart_revision: cart.revision,
    };

    const { status: saleStatus, data: sale } = await api("/api/v1/sales", {
      method: "POST",
      token,
      expect: [201],
      headers: { "Idempotency-Key": idempotencyKey },
      body: saleBody,
    });
    evidence.created_data.sale_id = sale.id;
    check("LP02 sandbox charge returned 201", saleStatus === 201, { sale_id: sale.id, total });
    check("LP03 payment summary is LINE_PAY", sale.payment_method === "LINE_PAY");
    const tender = sale.tenders.find((row) => row.tender_type === "LINE_PAY");
    check(
      "LP04 tender amount and 1.5% fee are exact",
      tender?.amount === total && tender?.fee_amount === expectedFee,
      { expected_amount: total, actual_amount: tender?.amount, expected_fee: expectedFee, actual_fee: tender?.fee_amount },
    );

    const transaction = psql(
      `SELECT status || '|' || amount || '|' || refunded_amount || '|' || CASE WHEN transaction_id ~ '^[0-9]{15,}$' THEN 'valid' ELSE 'invalid' END FROM linepay_transactions WHERE sale_id=${Number(sale.id)}`,
    );
    check("LP05 persisted transaction is COMPLETE with a valid provider id", transaction === `COMPLETE|${total}|0|valid`);
    check(
      "LP06 LINE Pay sale creates no cash-drawer movement",
      psql(`SELECT COUNT(*) FROM cash_movements WHERE ref_type='sale' AND ref_id=${Number(sale.id)}`) === "0",
    );
    check(
      "LP07 charged serialized item is SOLD with zero ledger balance",
      psql(
        `SELECT si.status || '|' || COALESCE(SUM(CASE sm.direction WHEN 'IN' THEN sm.qty WHEN 'OUT' THEN -sm.qty ELSE sm.qty END),0) FROM serialized_items si LEFT JOIN stock_movements sm ON sm.serialized_item_id=si.id WHERE si.item_code='${acquisition.item_codes[0]}' GROUP BY si.id`,
      ) === "SOLD|0",
    );

    const replay = await api("/api/v1/sales", {
      method: "POST",
      token,
      expect: [201],
      headers: { "Idempotency-Key": idempotencyKey },
      body: saleBody,
    });
    check(
      "LP08 identical retry replays the same sale without a second charge",
      replay.data.id === sale.id &&
        psql(`SELECT COUNT(*) FROM linepay_transactions WHERE sale_id=${Number(sale.id)}`) === "1",
    );

    const conflict = await api("/api/v1/sales", {
      method: "POST",
      token,
      expect: [409],
      headers: { "Idempotency-Key": idempotencyKey },
      body: {
        ...saleBody,
        tenders: [
          {
            tender_type: "LINE_PAY",
            amount: String(Number(total) + 1),
            line_pay_one_time_key: oneTimeKey,
          },
        ],
      },
    });
    check(
      "LP09 same key with changed payload is rejected without another charge",
      conflict.status === 409 &&
        psql(`SELECT COUNT(*) FROM linepay_transactions WHERE sale_id=${Number(sale.id)}`) === "1",
    );

    // LP10：日結**現金**收入不得包含這筆 LINE Pay 銷售。只斷言 HTTP 200 等於什麼都沒驗——
    // 報表就算把它錯算進現金也會通過（Codex 第二輪指出的空斷言）。改為以「作廢前後的
    // 現金銷售額不變」證明它從未被計入。
    const today = taipeiDateForScript();
    const { data: dailyCash } = await api(`/api/v1/reports/daily-cash?date=${today}`, {
      token,
      expect: [200],
    });
    const cashSalesBefore = String(dailyCash.total_cash_sales ?? "");
    const drawerMovements = psql(
      `SELECT COUNT(*) FROM cash_movements WHERE ref_type='sale' AND ref_id=${Number(sale.id)}`,
    );
    check(
      "LP10 daily cash report excludes this non-cash sale",
      cashSalesBefore !== "" && drawerMovements === "0",
      { cash_sales: cashSalesBefore, drawer_movements_for_sale: drawerMovements },
    );

    const { status: voidStatus, data: voided } = await api(`/api/v1/sales/${sale.id}/void`, {
      method: "POST",
      token,
      expect: [200],
    });
    check("LP11 sale void completed a real sandbox refund", voidStatus === 200 && voided.status === "VOIDED");
    check(
      "LP12 transaction is REFUNDED for the full original amount",
      psql(`SELECT status || '|' || refunded_amount FROM linepay_transactions WHERE sale_id=${Number(sale.id)}`) === `REFUNDED|${total}`,
    );
    check(
      "LP13 exactly one successful refund attempt exists",
      psql(
        `SELECT COUNT(*) || '|' || COALESCE(SUM(amount),0) FROM linepay_refund_attempts WHERE order_id=(SELECT order_id FROM linepay_transactions WHERE sale_id=${Number(sale.id)}) AND status='SUCCEEDED'`,
      ) === `1|${total}`,
    );
    check(
      "LP14 refund/void creates no cash-drawer movement",
      psql(`SELECT COUNT(*) FROM cash_movements WHERE ref_type IN ('sale','sale_void') AND ref_id=${Number(sale.id)}`) === "0",
    );
    check(
      "LP15 void restores serialized inventory exactly",
      psql(
        `SELECT si.status || '|' || COALESCE(SUM(CASE sm.direction WHEN 'IN' THEN sm.qty WHEN 'OUT' THEN -sm.qty ELSE sm.qty END),0) FROM serialized_items si LEFT JOIN stock_movements sm ON sm.serialized_item_id=si.id WHERE si.item_code='${acquisition.item_codes[0]}' GROUP BY si.id`,
      ) === "IN_STOCK|1",
    );

    const beforeRetryAttempts = psql(
      `SELECT COUNT(*) FROM linepay_refund_attempts WHERE order_id=(SELECT order_id FROM linepay_transactions WHERE sale_id=${Number(sale.id)})`,
    );
    const secondVoid = await api(`/api/v1/sales/${sale.id}/void`, {
      method: "POST",
      token,
      expect: [409],
    });
    const afterRetryAttempts = psql(
      `SELECT COUNT(*) FROM linepay_refund_attempts WHERE order_id=(SELECT order_id FROM linepay_transactions WHERE sale_id=${Number(sale.id)})`,
    );
    check(
      "LP16 duplicate void is rejected without a second refund attempt",
      secondVoid.status === 409 && beforeRetryAttempts === afterRetryAttempts,
    );

    const refreshed = await api(`/api/v1/sales/${sale.id}`, { token, expect: [200] });
    check(
      "LP17 re-query after refund remains VOIDED/REFUNDED",
      refreshed.data.status === "VOIDED" &&
        psql(`SELECT status FROM linepay_transactions WHERE sale_id=${Number(sale.id)}`) === "REFUNDED",
    );
  } catch (error) {
    evidence.error = error instanceof Error ? error.message : String(error);
    console.error(`ABORT ${evidence.error}`);
  } finally {
    if (token) {
      try {
        const restore = {};
        if (!settingsWasEnabled) restore.linepay_enabled = false;
        if (originalFeePct !== null && originalFeePct !== "0.0150") {
          restore.linepay_fee_pct = originalFeePct;
        }
        if (Object.keys(restore).length > 0) {
          await api("/api/v1/settings", { method: "PATCH", token, body: restore });
        }
        evidence.setting_restored = true;
        evidence.settings_restored_fields = Object.keys(restore);
      } catch (error) {
        evidence.setting_restored = false;
        evidence.setting_restore_error = error instanceof Error ? error.message : String(error);
      }
    }
    evidence.summary = {
      total: results.length,
      passed: results.filter((row) => row.pass).length,
      failed: results.filter((row) => !row.pass).length,
    };
    writeFileSync(OUT, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
  }

  if (evidence.error || results.some((row) => !row.pass)) process.exitCode = 1;
}

await main();
