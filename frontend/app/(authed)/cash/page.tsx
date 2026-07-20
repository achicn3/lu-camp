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
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import { formatNtd, parseNtd } from "@/lib/money";

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

function AdjustCard({ sessionId, onDone }: { sessionId: number; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const mutation = useMutation({
    mutationFn: async (input: { amount: number; note: string }) => {
      const { data, error: apiError } = await api.POST(
        "/api/v1/cash-sessions/{session_id}/movements",
        {
          params: { path: { session_id: sessionId } },
          body: { type: "MANUAL_ADJUST", amount: String(input.amount), note: input.note },
        },
      );
      if (!data) throw new Error(extractDetail(apiError) ?? "調整失敗");
      return data;
    },
    onSuccess: () => {
      setDone(true);
      onDone();
    },
    onError: (err: Error) => setError(err.message),
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setDone(false);
    const form = new FormData(event.currentTarget);
    const amount = parseAmountInput(String(form.get("amount")), { allowNegative: true });
    if (amount === null) {
      setError("請輸入非零整數金額");
      return;
    }
    const note = String(form.get("note")).trim();
    if (!note) {
      setError("請填寫事由（留痕）");
      return;
    }
    mutation.mutate({ amount, note });
    event.currentTarget.reset();
  }

  return (
    <form className="card" onSubmit={onSubmit}>
      <h2>現金手動調整</h2>
      <p className="hint">敏感操作將寫入稽核（誰/何時/金額/事由）。</p>
      <label className="field">
        <span className="field-label">調整金額（可負）</span>
        <input name="amount" inputMode="numeric" required />
      </label>
      <label className="field">
        <span className="field-label">事由</span>
        <input name="note" required />
      </label>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {done && <p className="form-success">已調整</p>}
      <button type="submit" className="btn-primary" disabled={mutation.isPending}>
        送出調整
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
                  {new Date(movement.created_at).toLocaleTimeString("zh-TW", {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
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

function CloseCard({
  sessionId,
  onClosed,
}: {
  sessionId: number;
  onClosed: (closed: CashSession) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async (counted: number) => {
      const { data, error: apiError } = await api.POST("/api/v1/cash-sessions/{session_id}/close", {
        params: { path: { session_id: sessionId } },
        body: { counted_amount: String(counted) },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "結帳失敗");
      return data;
    },
    onSuccess: onClosed,
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

function ClosedSummary({ closed, onReopen }: { closed: CashSession; onReopen: () => void }) {
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
                <dd>{new Date(open.opened_at).toLocaleString("zh-TW")}</dd>
              </div>
            </dl>
          </div>
          {session?.role === "MANAGER" && <AdjustCard sessionId={open.id} onDone={refresh} />}
          <AdjustmentHistory sessionId={open.id} />
          <CloseCard
            sessionId={open.id}
            onClosed={(closed) => {
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
