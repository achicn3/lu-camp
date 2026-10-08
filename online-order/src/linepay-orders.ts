// 線上 LINE Pay 付款流程（docs/44 §4.4、§4.4.2、§4.5 C2／C7／C10；O5a）。
//
// 客人頁按「LINE Pay 付款」→ 這裡向 LINE Pay 要付款連結 → 客人在 LINE Pay 授權後被導回自己的訂單頁 →
// 訂單頁呼叫請款。請款前先以條件更新把 PENDING 改成 CONFIRMING（只有一個請求贏，C2），確認限量保留仍有效
// （C10，過期就不請款＝不扣錢）。請款結果不明（逾時、連不上）維持 CONFIRMING、不判失敗（C7），由 POS 拉單時
// 的補查收斂；客人授權後沒回到訂單頁的單，補查看到「已授權」也會代為請款。
import { error, json, sha256Hex } from "./http";
import { checkPayment, confirmPayment, type LinePayConfig, linePayConfig, requestPayment } from "./linepay";

const TOKEN = /^[A-Za-z0-9_-]{32,64}$/;
const RECHECK_MS = 30_000;
// 保留到期前多久就不再開始付款／請款：POS 在到期時才放掉份數，兩邊時鐘差一點也不會撞。
const HOLD_MARGIN_MS = 60_000;
const RECONCILE_BATCH = 3;
// 補查跟著 POS 拉單一起跑：單次 LINE Pay 呼叫最多等 4 秒、整輪超過 6 秒就不再開始下一筆，
// 免得 LINE Pay 慢的時候拖住拉單（POS 只等 20 秒），連現金單都進不來（Codex O5 第二輪）。
const RECONCILE_CALL_MS = 4_000;
const RECONCILE_BUDGET_MS = 6_000;

interface PaymentRow {
  id: string;
  total: number;
  payment_method: string;
  payment_status: string;
  hold_status: string;
  sync_status: string;
  linepay_attempt: number;
  linepay_transaction_id: string | null;
  linepay_payment_url: string | null;
  hold_expires_at: number | null;
  needs_hold: number;
}

const SELECT_PAYMENT =
  "SELECT id, total, payment_method, payment_status, hold_status, sync_status, linepay_attempt, " +
  "linepay_transaction_id, linepay_payment_url, hold_expires_at, EXISTS (SELECT 1 FROM order_lines l WHERE l.order_id = orders.id AND l.limited = 1) " +
  "AS needs_hold FROM orders";

async function byToken(env: Env, storeId: number, token: string): Promise<PaymentRow | null> {
  if (!TOKEN.test(token)) return null;
  const hash = await sha256Hex(new TextEncoder().encode(token));
  return env.DB.prepare(`${SELECT_PAYMENT} WHERE store_id = ? AND token_hash = ?`).bind(storeId, hash).first<PaymentRow>();
}

/** 有限量品項的單：POS 保留成功（HELD）且離到期還有 1 分鐘以上才能付；被拒、過期、取消都不行。 */
function holdProblem(row: PaymentRow, now = Date.now()): string | null {
  if (row.sync_status === "VOIDED") return "order_cancelled";
  if (row.needs_hold !== 1) return null;
  if (row.hold_status !== "HELD") return row.hold_status === "HOLD_REQUESTED" ? "hold_pending" : "hold_expired";
  if (row.hold_expires_at !== null && row.hold_expires_at - HOLD_MARGIN_MS <= now) return "hold_expired";
  return null;
}

/** 請款前最後一次檢查保留（與改成 CONFIRMING 同一句 SQL，POS 的到期回報插不進來）。 */
const HOLD_STILL_VALID =
  "(NOT EXISTS (SELECT 1 FROM order_lines l WHERE l.order_id = orders.id AND l.limited = 1) OR " +
  "(hold_status = 'HELD' AND sync_status != 'VOIDED' AND (hold_expires_at IS NULL OR hold_expires_at > ?)))";

async function record(env: Env, storeId: number, id: string, from: string, to: string, now: number): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO order_events (store_id, order_id, kind, from_state, to_state, source, at) VALUES (?, ?, ?, ?, ?, 'linepay', ?)",
  ).bind(storeId, id, to, from, to, now).run();
}

/** 條件更新付款狀態；回傳是否真的改到（別的請求先改了就是 false）。 */
async function move(
  env: Env, storeId: number, row: PaymentRow, from: string[], to: string, extra: Record<string, unknown> = {}, now = Date.now(),
  guard: { sql: string; binds: unknown[] } | null = null,
): Promise<boolean> {
  const sets = Object.keys(extra).map((k) => `${k} = ?`);
  const result = await env.DB.prepare(
    `UPDATE orders SET payment_status = ?, updated_at = ?, row_version = row_version + 1${sets.map((s) => `, ${s}`).join("")} ` +
      `WHERE store_id = ? AND id = ? AND payment_status IN (${from.map(() => "?").join(",")})` +
      // 綁定是哪一次付款、哪一筆交易：中途客人取消又重付，舊的處理不能改到新的付款（Codex O5 第二輪）。
      " AND linepay_attempt = ? AND linepay_transaction_id IS ?" +
      (guard ? ` AND ${guard.sql}` : ""),
  ).bind(
    to, now, ...Object.values(extra), storeId, row.id, ...from,
    row.linepay_attempt, row.linepay_transaction_id, ...(guard?.binds ?? []),
  ).run();
  if (result.meta.changes !== 1) return false;
  if (!from.includes(to) || from.length > 1) await record(env, storeId, row.id, row.payment_status, to, now);
  return true;
}

export async function startLinePay(req: Request, env: Env, storeId: number, token: string): Promise<Response> {
  const config = linePayConfig(env);
  if (config === null) return error("linepay_unavailable", 422);
  const row = await byToken(env, storeId, token);
  if (row === null) return error("not_found", 404);
  if (row.payment_method !== "LINE_PAY") return error("not_linepay", 409);
  if (row.payment_status === "PAID") return error("already_paid", 409);
  if (!["UNPAID", "PENDING"].includes(row.payment_status)) return error("payment_in_progress", 409);
  const problem = holdProblem(row);
  if (problem !== null) return error(problem, 409);
  // 付款中再按一次（或開了兩個分頁）：沿用原連結，不另開交易——另開的話，前一筆若剛好授權請款，
  // 交易號會被新的蓋掉，店內記錯交易、之後退不了款（Codex O5 第一輪）。
  if (row.payment_status === "PENDING" && row.linepay_payment_url !== null) {
    return json({ payment_url: row.linepay_payment_url });
  }
  const attempt = row.linepay_attempt + 1;
  if (!(await move(env, storeId, row, ["UNPAID"], "PENDING", {
    linepay_attempt: attempt, linepay_result: null, linepay_transaction_id: null, linepay_payment_url: null,
  }))) {
    return error("payment_in_progress", 409);
  }
  const origin = new URL(req.url).origin;
  const reply = await requestPayment(config, {
    orderId: `${row.id}-${attempt}`,
    amount: row.total,
    productName: "露坑線上訂單",
    confirmUrl: `${origin}/order/${token}?linepay=return`,
    cancelUrl: `${origin}/order/${token}?linepay=cancel`,
  });
  if (reply.code !== "0000" || reply.transactionId === null || reply.paymentUrl === null) {
    // 連不到付款頁：沒有任何授權發生，回到未付款讓客人重試或改付現。
    await move(env, storeId, { ...row, payment_status: "PENDING", linepay_attempt: attempt, linepay_transaction_id: null },
      ["PENDING"], "UNPAID", { linepay_result: "FAILED" });
    return error("linepay_request_failed", 502);
  }
  // 只寫進「還是這一次、還在付款中、還沒有交易號」的單：晚到的舊回應不能改到新的付款。
  await env.DB.prepare(
    "UPDATE orders SET linepay_transaction_id = ?, linepay_payment_url = ?, linepay_checked_at = NULL " +
      "WHERE store_id = ? AND id = ? AND linepay_attempt = ? AND payment_status = 'PENDING' " +
      "AND linepay_transaction_id IS NULL",
  ).bind(reply.transactionId, reply.paymentUrl, storeId, row.id, attempt).run();
  return json({ payment_url: reply.paymentUrl });
}

/** 請款（導回或補查共用）。回傳客人頁該看到的結果。 */
async function capture(env: Env, storeId: number, config: LinePayConfig, row: PaymentRow): Promise<"PAID" | "CONFIRMING" | "PENDING" | "EXPIRED" | "FAILED"> {
  const tx = row.linepay_transaction_id;
  if (tx === null) return "FAILED";
  if (holdProblem(row) !== null) {
    await move(env, storeId, row, ["PENDING"], "UNPAID", { linepay_result: "EXPIRED" });
    return "EXPIRED";
  }
  const now = Date.now();
  const claimed = await move(env, storeId, row, ["PENDING"], "CONFIRMING", { linepay_checked_at: now }, now, {
    sql: HOLD_STILL_VALID, binds: [now + HOLD_MARGIN_MS],
  });
  if (!claimed) {
    // 沒搶到：可能別的請求正在請款，也可能保留剛好在這一刻到期——重讀判斷。
    const fresh = await env.DB.prepare(`${SELECT_PAYMENT} WHERE store_id = ? AND id = ?`).bind(storeId, row.id).first<PaymentRow>();
    if (fresh !== null && fresh.payment_status === "PENDING" && holdProblem(fresh) !== null) {
      await move(env, storeId, fresh, ["PENDING"], "UNPAID", { linepay_result: "EXPIRED" });
      return "EXPIRED";
    }
    return "CONFIRMING";
  }
  const confirming = { ...row, payment_status: "CONFIRMING" };
  const reply = await confirmPayment(config, tx, row.total);
  if (reply.code === "0000") {
    await move(env, storeId, confirming, ["CONFIRMING"], "PAID");
    return "PAID";
  }
  if (reply.code === null) return "CONFIRMING"; // 結果不明（C7）：留給補查
  return settleByCheck(env, storeId, config, confirming);
}

/** 用查詢 API 收斂：0123 已完成、0000 客人還沒付（回到 PENDING）、其他明確失敗。 */
async function settleByCheck(env: Env, storeId: number, config: LinePayConfig, row: PaymentRow): Promise<"PAID" | "CONFIRMING" | "PENDING" | "FAILED"> {
  const check = await checkPayment(config, row.linepay_transaction_id ?? "");
  if (check.code === "0123") {
    await move(env, storeId, row, ["CONFIRMING", "PENDING"], "PAID");
    return "PAID";
  }
  if (check.code === "0000") {
    await move(env, storeId, row, ["CONFIRMING"], "PENDING");
    return "PENDING";
  }
  if (check.code === null || check.code === "0110") return "CONFIRMING";
  await move(env, storeId, row, ["CONFIRMING"], "UNPAID", { linepay_result: "FAILED" });
  return "FAILED";
}

export async function confirmLinePay(env: Env, storeId: number, token: string): Promise<Response> {
  const config = linePayConfig(env);
  if (config === null) return error("linepay_unavailable", 422);
  const row = await byToken(env, storeId, token);
  if (row === null) return error("not_found", 404);
  if (row.payment_status === "PAID") return json({ status: "PAID" });
  if (row.payment_status === "CONFIRMING") return json({ status: "CONFIRMING" }, 202);
  if (row.payment_status !== "PENDING") return error("not_pending", 409);
  const outcome = await capture(env, storeId, config, row);
  if (outcome === "PAID") return json({ status: "PAID" });
  if (outcome === "CONFIRMING") return json({ status: "CONFIRMING" }, 202);
  if (outcome === "PENDING") return json({ status: "PENDING" }, 202);
  return error(outcome === "EXPIRED" ? "hold_expired" : "linepay_failed", 409);
}

export async function cancelLinePay(env: Env, storeId: number, token: string): Promise<Response> {
  const row = await byToken(env, storeId, token);
  if (row === null) return error("not_found", 404);
  if (row.payment_status === "UNPAID") return json({ status: "UNPAID" });
  if (!(await move(env, storeId, row, ["PENDING"], "UNPAID", { linepay_result: "CANCELLED" }))) {
    return error("not_pending", 409);
  }
  return json({ status: "UNPAID" });
}

/** POS 拉單時補查（C7）：確認中、或客人授權後沒回訂單頁的付款，每筆最多每 30 秒查一次。 */
export async function reconcileLinePay(env: Env, storeId: number, now = Date.now()): Promise<void> {
  const base = linePayConfig(env);
  if (base === null) return;
  const config: LinePayConfig = { ...base, timeoutMs: RECONCILE_CALL_MS };
  const started = Date.now();
  const due = await env.DB.prepare(
    `${SELECT_PAYMENT} WHERE store_id = ? AND payment_method = 'LINE_PAY' AND payment_status IN ('PENDING', 'CONFIRMING') ` +
      "AND linepay_transaction_id IS NOT NULL AND COALESCE(linepay_checked_at, 0) < ? " +
      // 付款中的客人可能還在 LINE Pay 頁面上：發起 30 秒後才去查；確認中的單照查詢間隔查。
      "AND (payment_status = 'CONFIRMING' OR updated_at < ?) " +
      "ORDER BY updated_at LIMIT ?",
  ).bind(storeId, now - RECHECK_MS, now - RECHECK_MS, RECONCILE_BATCH).all<PaymentRow>();
  for (const row of due.results) {
    if (Date.now() - started > RECONCILE_BUDGET_MS) break;
    await env.DB.prepare("UPDATE orders SET linepay_checked_at = ? WHERE store_id = ? AND id = ?").bind(now, storeId, row.id).run();
    if (row.payment_status === "CONFIRMING") {
      const check = await checkPayment(config, row.linepay_transaction_id ?? "");
      if (check.code === "0110") {
        // 授權了但沒請款成功：退回 PENDING 再走一次請款
        if (await move(env, storeId, row, ["CONFIRMING"], "PENDING")) await capture(env, storeId, config, { ...row, payment_status: "PENDING" });
        continue;
      }
      await settleByCheck(env, storeId, config, row);
      continue;
    }
    const check = await checkPayment(config, row.linepay_transaction_id ?? "");
    if (check.code === "0110") await capture(env, storeId, config, row);
    else if (check.code === "0123") await move(env, storeId, row, ["PENDING"], "PAID");
  }
}
