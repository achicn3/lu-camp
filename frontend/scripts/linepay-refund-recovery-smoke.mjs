// LINE Pay 退款對帳與自動補帳瀏覽器煙霧：真 backend + 真 Postgres。
// 測試 DB 準備一筆真實可復原的未定退款；店長於 /sales 確認平台已退，
// 畫面立即禁止再退一次，並驗證每分鐘的背景排程會補完本地退貨。
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const DB_CONTAINER = process.env.SMOKE_DB_CONTAINER ?? "lu-camp-db-1";
const DB_NAME = process.env.SMOKE_DB_NAME ?? "lucamp_e2e";
const DB_USER = process.env.SMOKE_DB_USER ?? "lucamp";
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "linepay-refund-recovery");
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";
const results = [];

mkdirSync(SHOTS, { recursive: true });

function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

function psql(sql) {
  return execFileSync(
    "docker",
    ["exec", "-i", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME, "-qtA", "-c", sql],
    { encoding: "utf8" },
  ).trim();
}

function sqlText(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

// Python json.dumps(sort_keys=True, ensure_ascii=False) 的簡化對等格式；
// 後端 _refund_identity 以這串文字做 SHA-256，煙霧資料必須交出同一把鍵。
function pythonJson(value) {
  if (value === null || typeof value === "boolean" || typeof value === "number") {
    return JSON.stringify(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(pythonJson).join(", ")}]`;
  const entries = Object.entries(value).sort(([left], [right]) =>
    left < right ? -1 : left > right ? 1 : 0,
  );
  return `{${entries.map(([key, item]) => `${JSON.stringify(key)}: ${pythonJson(item)}`).join(", ")}}`;
}

async function apiJson(path, { method = "GET", token, body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  return response.json();
}

async function waitForRecovery(attemptId, timeoutMs = 75000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const state = psql(
      `SELECT COALESCE(recovered_at::text, '') || '|' || recovery_attempts || '|' || ` +
        `COALESCE(recovery_error, '') FROM linepay_refund_attempts WHERE id=${attemptId}`,
    );
    if (state.split("|")[0] !== "") return state;
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw new Error(`等待背景自動補帳超時（attempt ${attemptId}）`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 950 } });
page.on("pageerror", (error) => ok("頁面 JS 錯誤", false, String(error)));

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: PASS },
  });
  const claims = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString("utf8"));
  const storeId = Number(claims.store_id);
  const actorUserId = Number(claims.sub);
  if (!Number.isInteger(storeId) || !Number.isInteger(actorUserId)) {
    throw new Error("登入 token 缺少 store_id/sub");
  }

  const runId = Date.now();
  const orderId = `SMOKE-REFUND-${runId}`;
  const reason = `瀏覽器煙霧對帳-${runId}`;
  const ids = psql(
    "SELECT nextval('serialized_items_id_seq'), nextval('sales_id_seq'), " +
      "nextval('sale_lines_id_seq'), nextval('linepay_refund_attempts_id_seq')",
  )
    .split("|")
    .map(Number);
  const [itemId, saleId, saleLineId, attemptId] = ids;
  if (ids.some((id) => !Number.isInteger(id))) throw new Error(`無法預取測試 id：${ids}`);

  const identity = pythonJson({
    lines: [{ qty: 1, sale_line_id: saleLineId }],
    prior_returned: [],
    reason,
    sale_id: saleId,
  });
  const refundIdentity = createHash("sha256").update(identity).digest("hex").slice(0, 32);
  const refundKey = `s${storeId}:return:${refundIdentity}`;
  const recoveryPayload = JSON.stringify({
    sale_id: saleId,
    lines: [{ sale_line_id: saleLineId, qty: 1 }],
    reason,
    actor_user_id: actorUserId,
    idempotency_key: `smoke-return-${runId}`,
    taiwan_pay_refund_confirmed: false,
    invoice_recalled: false,
    consent_signature_task_id: null,
    unreturned_gift_note: null,
    manual_paper_disposed: false,
  });
  psql(`
    BEGIN;
    INSERT INTO serialized_items
      (id, store_id, item_code, name, grade, ownership_type, acquisition_cost,
       listed_price, status, sold_date)
    VALUES
      (${itemId}, ${storeId}, ${sqlText(`REFUND-SMOKE-${runId}`)},
       ${sqlText("自動補帳煙霧商品")}, 'A', 'OWNED', 60, 137, 'SOLD', now());
    INSERT INTO sales
      (id, store_id, clerk_user_id, subtotal, tax, total, payment_method,
       invoice_status, status, awarded_points)
    VALUES
      (${saleId}, ${storeId}, ${actorUserId}, 130, 7, 137, 'LINE_PAY',
       'NOT_ISSUED', 'COMPLETED', 0);
    INSERT INTO sale_lines
      (id, store_id, sale_id, line_type, serialized_item_id, description, qty,
       unit_price, line_total, discount_amount, line_kind, manual_discount_amount,
       net_amount, cost_snapshot)
    VALUES
      (${saleLineId}, ${storeId}, ${saleId}, 'SERIALIZED', ${itemId},
       ${sqlText("自動補帳煙霧商品")}, 1, 137, 137, 0, 'NORMAL', 0, 137, 60);
    INSERT INTO sale_tenders (store_id, sale_id, tender_type, amount, fee_amount)
    VALUES (${storeId}, ${saleId}, 'LINE_PAY', 137, 0);
    INSERT INTO linepay_transactions
      (store_id, sale_id, order_id, transaction_id, status, amount, refunded_amount, raw_response)
    VALUES
      (${storeId}, ${saleId}, ${sqlText(orderId)}, ${sqlText(String(runId))},
       'COMPLETE', 137, 0, '{}'::jsonb);
    INSERT INTO linepay_refund_attempts
      (id, store_id, refund_key, order_id, amount, status, return_code,
       recovery_kind, recovery_payload, recovery_attempts)
    VALUES
      (${attemptId}, ${storeId}, ${sqlText(refundKey)}, ${sqlText(orderId)}, 137, 'PENDING', NULL,
       'RETURN', ${sqlText(recoveryPayload)}::jsonb, 0);
    COMMIT;
  `);
  ok("建立可真實復原的未定退款", true, `sale #${saleId} / attempt #${attemptId}`);

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', PASS);
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 15000 });
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });

  const row = page.locator("tr").filter({ hasText: orderId });
  await row.waitFor();
  ok("店長看得到待查平台的退款", await row.getByText("待查平台").isVisible());
  await page.screenshot({ path: join(SHOTS, "01-pending-refund.png"), fullPage: true });

  await row.getByRole("button", { name: "確認已退款" }).click();
  await row.getByText("不需再次退款", { exact: true }).waitFor();
  ok("確認平台已退款後，畫面明確禁止再退一次", true);
  ok(
    "畫面改為本地自動復原狀態",
    await row.getByText(/本地自動復原中|自動復原失敗/).isVisible(),
  );
  await page.screenshot({ path: join(SHOTS, "02-provider-succeeded.png"), fullPage: true });

  const recoveryState = await waitForRecovery(attemptId);
  const finalState = psql(`
    SELECT s.status || '|' || t.status || '|' || t.refunded_amount || '|' || i.status || '|' ||
           (SELECT count(*) FROM returns r WHERE r.sale_id=s.id)
      FROM sales s
      JOIN linepay_transactions t ON t.sale_id=s.id
      JOIN serialized_items i ON i.id=${itemId}
     WHERE s.id=${saleId};
  `);
  ok(
    "系統自動補完本地退貨，且沒有再送一次退款",
    finalState === "RETURNED|REFUNDED|137|IN_STOCK|1",
    `${finalState}; recovery=${recoveryState}`,
  );
} catch (error) {
  ok("煙霧流程例外", false, String(error));
} finally {
  await browser.close();
}

const failed = results.filter((result) => !result.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
