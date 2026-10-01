"use client";
// /cash 現金對帳（docs/10 §5）：開帳（零用金）→ 開帳中（資訊＋MANAGER 手動調整）→
// 結帳（實點 vs 應有＋差異）。異動清單待後端 GET 端點（docs/04 缺口，已回報）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
  useState,
} from "react";

import { parseAmountInput } from "@/features/cash/money-input";
import { type DailyStockEntry, useDailyStock } from "@/features/menu/DailyStockPanel";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import { formatTaipeiDateTime, formatTaipeiTime } from "@/lib/datetime";
import {
  canDiscardIdempotencyKey,
  clearPendingCashAdjustment,
  loadPendingCashAdjustment,
  savePendingCashAdjustment,
} from "@/lib/idempotency";
import { formatNtd, parseNtd } from "@/lib/money";
import { newIdempotencyKey } from "@/lib/uuid";

type CashSession = components["schemas"]["CashSessionRead"];
type CashMovement = components["schemas"]["CashMovementRead"];

function MoneyText({ value }: { value: string | null | undefined }) {
  if (value === null || value === undefined) return <span className="money">—</span>;
  const parsed = parseNtd(value);
  return <span className="money">{parsed === null ? value : formatNtd(parsed)}</span>;
}

function blockNonDigitKey(
  event: KeyboardEvent<HTMLInputElement>,
  onRejected: () => void,
) {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key.length === 1 && !/^\d$/.test(event.key)) {
    event.preventDefault();
    onRejected();
  }
}

function blockNonDigitPaste(
  event: ClipboardEvent<HTMLInputElement>,
  onRejected: () => void,
) {
  if (!/^\d+$/.test(event.clipboardData.getData("text"))) {
    event.preventDefault();
    onRejected();
  }
}

function OpenSessionCard({ onOpened }: { onOpened: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [formatRejected, setFormatRejected] = useState(false);
  const rejectOpeningFormat = () => {
    setFormatRejected(true);
    setError("請輸入整數金額，不可使用科學記號");
  };
  const mutation = useMutation({
    mutationFn: async (openingFloat: number) => {
      const { data, error: apiError } = await api.POST("/api/v1/cash-sessions/open", {
        body: { opening_float: String(openingFloat) },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "開帳失敗");
      return data;
    },
    onSuccess: onOpened,
    onError: (err: Error) => setError(err.message),
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (formatRejected) {
      setError("請輸入整數金額，不可使用科學記號");
      return;
    }
    const raw = String(new FormData(event.currentTarget).get("opening_float"));
    const amount = parseAmountInput(raw, { allowZero: true });
    if (amount === null) {
      setError("請輸入整數金額");
      return;
    }
    mutation.mutate(amount);
  }

  return (
    <form className="card" onSubmit={onSubmit}>
      <h2>開帳</h2>
      <label className="field">
        <span className="field-label">開帳零用金</span>
        <input
          name="opening_float"
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          onChange={(event) => {
            if (event.currentTarget.value === "") {
              setFormatRejected(false);
              setError(null);
            }
          }}
          onKeyDown={(event) => blockNonDigitKey(event, rejectOpeningFormat)}
          onPaste={(event) => blockNonDigitPaste(event, rejectOpeningFormat)}
          required
        />
      </label>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <button type="submit" className="btn-primary" disabled={mutation.isPending}>
        開帳
      </button>
    </form>
  );
}

// 補入／取出現金（2026-10-01 改版，原「現金手動調整」）。
//
// 金額是**加減**到「應有現金」上，不是把應有現金改成這個數字——原本的「調整金額（可負）」
// 要自己打負號、也看不出是累加，店主反映不好用。改成：先選放入或取出、金額只填正數、
// 送出前顯示「目前應有 → 調整後」。送出的仍是同一種異動（MANUAL_ADJUST，正＝放入、負＝取出），
// 回應遺失時「以原金額與原因重試」的防重複保護不變。
const ADJUST_REASONS = ["補零錢", "存銀行", "飯錢", "付小額雜支"] as const;

type AdjustDirection = "IN" | "OUT";

function AdjustCard({ sessionId, onDone }: { sessionId: number; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [direction, setDirection] = useState<AdjustDirection | null>(null);
  const [amountText, setAmountText] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  // 目前應有現金（僅店長可讀；店員結帳要先數錢再比對，不在店員畫面顯示）。
  const expected = useQuery({
    queryKey: ["cash-session", sessionId, "expected"],
    queryFn: async () => {
      const { data, error: apiError } = await api.GET(
        "/api/v1/cash-sessions/{session_id}/expected",
        { params: { path: { session_id: sessionId } } },
      );
      if (!data) throw new Error(extractDetail(apiError) ?? "讀取應有現金失敗");
      return data;
    },
  });
  const amount = parseAmountInput(amountText);
  const signed = amount === null || direction === null ? null : direction === "IN" ? amount : -amount;
  const expectedNow = expected.data ? parseNtd(expected.data.expected) : null;

  const mutation = useMutation({
    mutationFn: async (input: { amount: number; note: string }) => {
      const pending = loadPendingCashAdjustment(sessionId);
      if (pending != null && (pending.amount !== input.amount || pending.note !== input.note)) {
        throw new Error("上一筆現金調整狀態未確認，請以原金額與原因重試或先查核本班紀錄");
      }
      const entry = pending ?? { key: newIdempotencyKey(), ...input };
      if (pending == null) savePendingCashAdjustment(sessionId, entry);
      const { data, error: apiError, response } = await api.POST(
        "/api/v1/cash-sessions/{session_id}/movements",
        {
          params: {
            path: { session_id: sessionId },
            header: { "Idempotency-Key": entry.key },
          },
          body: { type: "MANUAL_ADJUST", amount: String(input.amount), note: input.note },
        },
      );
      if (!data) {
        if (canDiscardIdempotencyKey(response.status)) {
          clearPendingCashAdjustment(sessionId);
        }
        throw new Error(extractDetail(apiError) ?? "記錄失敗");
      }
      clearPendingCashAdjustment(sessionId);
      return input;
    },
    onSuccess: (input) => {
      setDone(
        `已記錄：${input.amount > 0 ? "放入" : "取出"} $${formatNtd(Math.abs(input.amount))}（${input.note}）`,
      );
      setDirection(null);
      setAmountText("");
      setNote("");
      void queryClient.invalidateQueries({ queryKey: ["cash-session", sessionId, "expected"] });
      onDone();
    },
    onError: (err: Error) => setError(err.message),
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setDone(null);
    if (direction === null) {
      setError("請選擇放入或取出");
      return;
    }
    if (amount === null) {
      setError("請輸入金額（正整數）");
      return;
    }
    const trimmed = note.trim();
    if (!trimmed) {
      setError("請填寫原因（會留下紀錄）");
      return;
    }
    mutation.mutate({ amount: direction === "IN" ? amount : -amount, note: trimmed });
  }

  return (
    <form className="card cash-adjust" onSubmit={onSubmit}>
      <h2>補入／取出現金</h2>
      <p className="hint">
        金額會<b>加到或扣掉</b>「應有現金」，不是把應有現金改成這個數字。每筆都會留下紀錄（誰、何時、金額、原因）。
      </p>
      {expectedNow !== null && (
        <p className="cash-adjust-expected">目前應有現金 ${formatNtd(expectedNow)}</p>
      )}
      <div className="cash-adjust-direction" role="radiogroup" aria-label="放入或取出">
        {(
          [
            ["IN", "放入現金"],
            ["OUT", "取出現金"],
          ] as const
        ).map(([value, label]) => (
          <label key={value} className={`cash-adjust-choice${direction === value ? " is-on" : ""}`}>
            <input
              type="radio"
              name="direction"
              value={value}
              checked={direction === value}
              onChange={() => setDirection(value)}
            />
            {label}
          </label>
        ))}
      </div>
      <label className="field">
        <span className="field-label">金額</span>
        <input
          name="amount"
          inputMode="numeric"
          value={amountText}
          onChange={(e) => setAmountText(e.target.value)}
        />
      </label>
      <div className="cash-adjust-reasons">
        {ADJUST_REASONS.map((reason) => (
          <button key={reason} type="button" className="btn-ghost" onClick={() => setNote(reason)}>
            {reason}
          </button>
        ))}
      </div>
      <label className="field">
        <span className="field-label">原因</span>
        <input name="note" value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {expectedNow !== null && signed !== null && (
        <p className="cash-adjust-after">調整後應有現金 ${formatNtd(expectedNow + signed)}</p>
      )}
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {done !== null && <p className="form-success">{done}</p>}
      <button type="submit" className="btn-primary" disabled={mutation.isPending}>
        送出
      </button>
    </form>
  );
}

function AdjustmentHistory({ sessionId }: { sessionId: number }) {
  const movements = useQuery({
    queryKey: ["cash-session", sessionId, "movements"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/cash-sessions/{session_id}/movements", {
        params: { path: { session_id: sessionId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取調整紀錄失敗");
      return data;
    },
  });
  const adjustments = (movements.data ?? []).filter(
    (movement): movement is CashMovement => movement.type === "MANUAL_ADJUST",
  );

  return (
    <section className="card cash-adjustments" aria-labelledby="cash-adjustments-title">
      <div className="cash-adjustments-head">
        <div>
          <h2 id="cash-adjustments-title">本班調整紀錄</h2>
          <p className="hint">最新一筆在前，金額與事由會保留供對帳查核。</p>
        </div>
        {!movements.isPending && !movements.isError && (
          <span className="cash-adjustments-count" aria-label={`${adjustments.length} 筆調整`}>
            {adjustments.length} 筆
          </span>
        )}
      </div>
      {movements.isPending ? (
        <p className="cash-adjustments-state">讀取調整紀錄中…</p>
      ) : movements.isError ? (
        <p role="alert" className="form-error cash-adjustments-state">
          {movements.error.message}
        </p>
      ) : adjustments.length === 0 ? (
        <p className="cash-adjustments-state">
          本班尚無手動調整；若有補入或取出現金，會在這裡顯示金額與事由。
        </p>
      ) : (
        <ol className="cash-adjustment-list">
          {adjustments.map((movement) => {
            const amount = parseNtd(movement.amount);
            const isIncrease = amount !== null && amount > 0;
            const displayedAmount = amount === null ? movement.amount : formatNtd(amount);
            return (
              <li className="cash-adjustment-row" key={movement.id}>
                <time dateTime={movement.created_at}>
                  {formatTaipeiTime(movement.created_at)}
                </time>
                <span className="cash-adjustment-note">{movement.note ?? "未填事由"}</span>
                <strong
                  className={`cash-adjustment-amount ${
                    isIncrease ? "cash-adjustment-amount--in" : "cash-adjustment-amount--out"
                  }`}
                >
                  {isIncrease ? "+" : ""}
                  {displayedAmount}
                </strong>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function stockKey(entry: DailyStockEntry): string {
  return `${entry.kind}-${entry.id}`;
}

/** 關帳時的報廢結果（docs/49 F4）：記到的、沒記到的（附原因）。 */
interface LeftoverWasteResult {
  wasted: string[];
  failed: string[];
}

/**
 * 每日限量的剩餘份數隔天會自動歸零：沒記成報廢的就默默消失，報廢統計會偏低。
 * 關帳時逐項用既有的「減少份數」記成報廢——它是原子操作、不會扣到負數，
 * 回應遺失後重送也只會被「已經是 0」擋下，不會重複報廢。
 */
async function wasteLeftovers(entries: DailyStockEntry[]): Promise<LeftoverWasteResult> {
  const result: LeftoverWasteResult = { wasted: [], failed: [] };
  for (const entry of entries) {
    try {
      const { data, error } = await api.POST(
        "/api/v1/menu-daily-stock/{kind}/{target_id}/adjust",
        {
          params: { path: { kind: entry.kind, target_id: entry.id } },
          body: { delta: -entry.remaining, reason: "WASTE" },
        },
      );
      if (data) result.wasted.push(`${entry.label} ${entry.remaining} 份`);
      else result.failed.push(`${entry.label}（${extractDetail(error) ?? "記錄失敗"}）`);
    } catch {
      result.failed.push(`${entry.label}（連線失敗）`);
    }
  }
  return result;
}

function CloseCard({
  sessionId,
  onClosed,
}: {
  sessionId: number;
  onClosed: (closed: CashSession, waste: LeftoverWasteResult) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  // 每日限量今天還有剩的（docs/49 F4）：預設全勾報廢；明天還能賣的取消勾選即可。不擋關帳。
  const dailyStock = useDailyStock();
  const leftovers = (dailyStock.data ?? []).filter(
    (entry) => entry.set_today && entry.remaining > 0,
  );
  const [keep, setKeep] = useState<Set<string>>(new Set());
  const mutation = useMutation({
    mutationFn: async (counted: number) => {
      const waste = await wasteLeftovers(
        leftovers.filter((entry) => !keep.has(stockKey(entry))),
      );
      const { data, error: apiError } = await api.POST("/api/v1/cash-sessions/{session_id}/close", {
        params: { path: { session_id: sessionId } },
        body: { counted_amount: String(counted) },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "結帳失敗");
      return { closed: data, waste };
    },
    onSuccess: ({ closed, waste }) => onClosed(closed, waste),
    onError: (err: Error) => setError(err.message),
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const raw = String(new FormData(event.currentTarget).get("counted_amount"));
    const counted = parseAmountInput(raw, { allowZero: true });
    if (counted === null) {
      setError("請輸入整數金額");
      return;
    }
    mutation.mutate(counted);
  }

  return (
    <form className="card" onSubmit={onSubmit}>
      <h2>結帳</h2>
      {leftovers.length > 0 && (
        <fieldset className="cash-leftovers">
          <legend>今日餐點還有剩（每日限量明天會歸零）</legend>
          <p className="hint">
            勾選的會在結帳時記成報廢；明天還能賣的請取消勾選，明天開店填份數時把它算進去。
          </p>
          {leftovers.map((entry) => {
            const key = stockKey(entry);
            return (
              <label key={key} className="field field-toggle">
                <input
                  type="checkbox"
                  checked={!keep.has(key)}
                  aria-label={`${entry.label} 剩 ${entry.remaining} 份，記成報廢`}
                  onChange={(e) =>
                    setKeep((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.delete(key);
                      else next.add(key);
                      return next;
                    })
                  }
                />
                <span className="field-label">
                  {entry.label} 剩 {entry.remaining} 份 → 報廢
                </span>
              </label>
            );
          })}
        </fieldset>
      )}
      <label className="field">
        <span className="field-label">實點金額</span>
        <input name="counted_amount" inputMode="numeric" required />
      </label>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <button type="submit" className="btn-primary" disabled={mutation.isPending}>
        結帳
      </button>
    </form>
  );
}

function ClosedSummary({
  closed,
  onReopen,
  waste,
}: {
  closed: CashSession;
  onReopen: () => void;
  waste: LeftoverWasteResult | null;
}) {
  const varianceValue = closed.variance === null ? null : parseNtd(closed.variance);
  return (
    <div className="card">
      <h2>已結帳</h2>
      <dl className="stat-list">
        <div className="stat">
          <dt>應有現金</dt>
          <dd>
            <MoneyText value={closed.expected_amount} />
          </dd>
        </div>
        <div className="stat">
          <dt>實點金額</dt>
          <dd>
            <MoneyText value={closed.counted_amount} />
          </dd>
        </div>
        <div className="stat">
          <dt>差異</dt>
          <dd className={varianceValue !== null && varianceValue !== 0 ? "variance-bad" : ""}>
            {closed.variance ?? "—"}
          </dd>
        </div>
      </dl>
      {varianceValue !== null && varianceValue !== 0 && (
        <p className="form-error">現金差異非零，已留紀錄；請依門市流程查核。</p>
      )}
      {waste !== null && waste.wasted.length > 0 && (
        <p className="hint">已記成報廢：{waste.wasted.join("、")}</p>
      )}
      {waste !== null && waste.failed.length > 0 && (
        <p role="alert" className="form-error">
          這幾項沒記成報廢，請到開店前檢查頁確認份數：{waste.failed.join("、")}
        </p>
      )}
      <button type="button" className="btn-primary" onClick={onReopen}>
        重新開帳
      </button>
    </div>
  );
}

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export default function CashPage() {
  const queryClient = useQueryClient();
  const [closedResult, setClosedResult] = useState<CashSession | null>(null);
  const [wasteResult, setWasteResult] = useState<LeftoverWasteResult | null>(null);
  const session = decodeSession();
  const current = useQuery({
    queryKey: ["cash-session", "current"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/cash-sessions/current");
      if (response.status === 200) return data ?? null;
      throw new Error(extractDetail(error) ?? "讀取開帳狀態失敗");
    },
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["cash-session"] });
  }

  if (current.isPending) return <p>載入中…</p>;
  if (current.isError)
    return (
      <p role="alert" className="form-error">
        {current.error.message}
      </p>
    );

  if (closedResult !== null) {
    return (
      <section>
        <h1 className="page-title">現金對帳</h1>
        <ClosedSummary
          closed={closedResult}
          waste={wasteResult}
          onReopen={() => {
            setClosedResult(null);
            refresh();
          }}
        />
      </section>
    );
  }

  const open = current.data;
  return (
    <section>
      <h1 className="page-title">現金對帳</h1>
      {open === null || open === undefined ? (
        <OpenSessionCard onOpened={refresh} />
      ) : (
        <div className="card-stack">
          <div className="card">
            <h2>
              <span className="badge-open">開帳中</span>
            </h2>
            <dl className="stat-list">
              <div className="stat">
                <dt>開帳零用金</dt>
                <dd>
                  <MoneyText value={open.opening_float} />
                </dd>
              </div>
              <div className="stat">
                <dt>開帳時間</dt>
                <dd>{formatTaipeiDateTime(open.opened_at)}</dd>
              </div>
            </dl>
          </div>
          {session?.role === "MANAGER" && <AdjustCard sessionId={open.id} onDone={refresh} />}
          <AdjustmentHistory sessionId={open.id} />
          <CloseCard
            sessionId={open.id}
            onClosed={(closed, waste) => {
              setWasteResult(waste);
              // 份數變了：開店檢查、POS 磚、菜單頁都要重讀。
              for (const key of [["menu-daily-stock"], ["menu-items"], ["opening-check"]]) {
                void queryClient.invalidateQueries({ queryKey: key });
              }
              // 同步失效快取：避免導航離開再回來時，殘留的 OPEN session 快取
              // 讓使用者對「已關帳的錢櫃」看到/操作結帳與調整控制（Codex P2）。
              setClosedResult(closed);
              refresh();
            }}
          />
        </div>
      )}
    </section>
  );
}
