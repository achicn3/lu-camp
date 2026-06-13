"use client";
// /pos 結帳（docs/10 §5、docs/16 §3.2）：掃碼加入購物車（序號品／散裝堆）→ 會員歸戶（選填）
// → 收款（現金／購物金／混合）→ 結帳 POST /sales →（完成後）詢問是否列印商品明細。
// einvoice_enabled=false 時發票區隱藏（顯示「本期不開票」），載具輸入待啟用後再開。
import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import {
  type CartLine,
  addLine,
  cartTotal,
  lineTotal,
  removeLine,
  setQty,
  toSaleLines,
} from "@/features/pos/cart";
import {
  type TenderMode,
  changeDue,
  resolvePlan,
  toTenders,
  validatePlan,
} from "@/features/pos/tender";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type SaleRead = components["schemas"]["SaleRead"];
type ContactRead = components["schemas"]["ContactRead"];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function Money({ value }: { value: number }) {
  return <span className="money">${formatNtd(value)}</span>;
}

// ── 掃碼加入購物車 ──
function ScanBar({ onResolved }: { onResolved: (line: CartLine) => void }) {
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async (code: string): Promise<CartLine> => {
      // 先試序號品，再試散裝堆（一格掃碼通吃，docs/10 §3）。
      const serialized = await api.GET(
        "/api/v1/serialized-items/by-code/{item_code}",
        {
          params: { path: { item_code: code } },
        },
      );
      if (serialized.response.status === 200 && serialized.data) {
        const item = serialized.data;
        if (item.status !== "IN_STOCK")
          throw new Error(`${item.item_code} 非在庫（不可售）`);
        const price = parseNtd(item.listed_price) ?? 0;
        return {
          key: `S:${item.item_code}`,
          lineType: "SERIALIZED",
          description: item.name,
          unitPrice: price,
          qty: 1,
          itemCode: item.item_code,
          maxQty: 1,
        };
      }
      const bulk = await api.GET("/api/v1/bulk-lots/by-code/{lot_code}", {
        params: { path: { lot_code: code } },
      });
      if (bulk.response.status === 200 && bulk.data) {
        const lot = bulk.data;
        if (lot.remaining_qty <= 0) throw new Error(`${lot.lot_code} 已售罄`);
        return {
          key: `B:${lot.id}`,
          lineType: "BULK_LOT",
          description: lot.name,
          unitPrice: parseNtd(lot.unit_price) ?? 0,
          qty: 1,
          bulkLotId: lot.id,
          maxQty: lot.remaining_qty,
        };
      }
      throw new Error(`找不到此條碼：${code}`);
    },
    onSuccess: (line) => {
      setError(null);
      onResolved(line);
    },
    onError: (err: Error) => setError(err.message),
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const code = String(new FormData(form).get("code")).trim();
    if (!code) return;
    mutation.mutate(code);
    form.reset();
  }

  return (
    <form className="pos-scan" onSubmit={onSubmit}>
      <label className="field">
        <span className="field-label">掃描或輸入條碼</span>
        {/* 櫃檯掃碼槍輸入，聚焦為核心操作（docs/10 §3）。 */}
        <input
          name="code"
          autoFocus
          inputMode="text"
          autoComplete="off"
          disabled={mutation.isPending}
        />
      </label>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </form>
  );
}

// ── 會員歸戶 ──
function MemberPanel({
  member,
  onSelect,
  onClear,
}: {
  member: ContactRead | null;
  onSelect: (c: ContactRead) => void;
  onClear: () => void;
}) {
  const [q, setQ] = useState("");
  const search = useMutation({
    mutationFn: async (query: string): Promise<ContactRead[]> => {
      const { data, error } = await api.GET("/api/v1/contacts", {
        params: { query: { q: query, role: "MEMBER", limit: 8 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "查詢失敗");
      return data;
    },
  });
  const balance = useQuery({
    queryKey: ["store-credit", member?.id],
    enabled: member !== null,
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/contacts/{contact_id}/store-credit",
        {
          params: { path: { contact_id: member!.id } },
        },
      );
      if (!data) throw new Error(extractDetail(error) ?? "讀取餘額失敗");
      return data;
    },
  });

  if (member !== null) {
    const bal = balance.data ? (parseNtd(balance.data.balance) ?? 0) : null;
    return (
      <div className="pos-member pos-member-selected">
        <div>
          <strong>{member.name}</strong>
          {member.phone && <span className="hint"> · {member.phone}</span>}
          <div className="hint">
            點數 {member.member_points} · 購物金餘額{" "}
            {bal === null ? "讀取中…" : <Money value={bal} />}
          </div>
        </div>
        <button type="button" className="btn-ghost" onClick={onClear}>
          取消歸戶
        </button>
      </div>
    );
  }

  return (
    <div className="pos-member">
      <form
        className="pos-member-search"
        onSubmit={(e) => {
          e.preventDefault();
          if (q.trim()) search.mutate(q.trim());
        }}
      >
        <label className="field">
          <span className="field-label">
            會員歸戶（選填；以購物金付款必填）
          </span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="姓名或電話"
            inputMode="text"
          />
        </label>
        <button type="submit" className="btn-ghost" disabled={search.isPending}>
          查詢會員
        </button>
      </form>
      {search.isError && (
        <p role="alert" className="form-error">
          {(search.error as Error).message}
        </p>
      )}
      {search.data && search.data.length === 0 && (
        <p className="hint">查無符合的會員。</p>
      )}
      {search.data && search.data.length > 0 && (
        <ul className="pos-member-results">
          {search.data.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => onSelect(c)}
              >
                {c.name}
                {c.phone ? ` · ${c.phone}` : ""}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── 收款 ──
function TenderPanel({
  total,
  hasMember,
  memberBalance,
  mode,
  setMode,
  cashInput,
  setCashInput,
  receivedInput,
  setReceivedInput,
}: {
  total: number;
  hasMember: boolean;
  memberBalance: number | null;
  mode: TenderMode;
  setMode: (m: TenderMode) => void;
  cashInput: string;
  setCashInput: (v: string) => void;
  receivedInput: string;
  setReceivedInput: (v: string) => void;
}) {
  const plan = resolvePlan(mode, total, parseNtd(cashInput) ?? 0);
  const validation = validatePlan(plan, total, { hasMember, memberBalance });
  const received = parseNtd(receivedInput);
  const change = received !== null ? changeDue(received, plan.cash) : null;
  return (
    <div className="pos-tender">
      <div className="pos-tender-modes" role="radiogroup" aria-label="收款方式">
        {(["CASH", "STORE_CREDIT", "MIXED"] as const).map((m) => (
          <label
            key={m}
            className={`pos-tender-mode ${mode === m ? "is-active" : ""}`}
          >
            <input
              type="radio"
              name="tender-mode"
              checked={mode === m}
              onChange={() => setMode(m)}
            />
            {m === "CASH" ? "現金" : m === "STORE_CREDIT" ? "購物金" : "混合"}
          </label>
        ))}
      </div>

      {mode === "MIXED" && (
        <label className="field">
          <span className="field-label">現金部分（其餘以購物金支付）</span>
          <input
            value={cashInput}
            onChange={(e) => setCashInput(e.target.value)}
            inputMode="numeric"
          />
        </label>
      )}
      {plan.storeCredit > 0 && (
        <p className="hint">
          購物金扣抵 <Money value={plan.storeCredit} />
          {memberBalance !== null && (
            <>
              {" "}
              · 餘額 <Money value={memberBalance} />
            </>
          )}
        </p>
      )}
      {plan.cash > 0 && (
        <label className="field">
          <span className="field-label">實收現金（找零輔助，不影響入帳）</span>
          <input
            value={receivedInput}
            onChange={(e) => setReceivedInput(e.target.value)}
            inputMode="numeric"
          />
        </label>
      )}
      {change !== null && change >= 0 && (
        <p className="pos-change">
          找零 <Money value={change} />
        </p>
      )}
      {validation.error !== null && (
        <p role="alert" className="form-error">
          {validation.error}
        </p>
      )}
    </div>
  );
}

// ── 完成後：列印商品明細對話框 ──
function PrintDialog({
  sale,
  onClose,
}: {
  sale: SaleRead;
  onClose: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [printed, setPrinted] = useState(false);
  const print = useMutation({
    mutationFn: async () => {
      const { data, error: apiError } = await api.POST(
        "/api/v1/sales/{sale_id}/print-detail",
        {
          params: { path: { sale_id: sale.id } },
        },
      );
      if (!data)
        throw new Error(extractDetail(apiError) ?? "列印失敗（請確認明細機）");
      return data;
    },
    onSuccess: () => {
      setError(null);
      setPrinted(true);
    },
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="列印商品明細"
    >
      <div className="card pos-dialog">
        <h2>列印商品明細？</h2>
        <p className="hint">
          完成結帳。可現在列印商品明細聯，或日後在交易紀錄補印。
        </p>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        {printed && <p className="form-success">已送出列印。</p>}
        <div className="pos-dialog-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={() => print.mutate()}
            disabled={print.isPending}
          >
            {print.isPending ? "列印中…" : printed ? "再印一次" : "列印明細"}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            {printed ? "完成" : "不用，完成"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function PosPage() {
  const [lines, setLines] = useState<CartLine[]>([]);
  const [member, setMember] = useState<ContactRead | null>(null);
  const [mode, setMode] = useState<TenderMode>("CASH");
  const [cashInput, setCashInput] = useState("");
  const [receivedInput, setReceivedInput] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [completed, setCompleted] = useState<SaleRead | null>(null);
  const [showDialog, setShowDialog] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState(() =>
    crypto.randomUUID(),
  );

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/settings");
      if (!data) throw new Error(extractDetail(error) ?? "讀取設定失敗");
      return data;
    },
  });
  const balanceQuery = useQuery({
    queryKey: ["store-credit", member?.id],
    enabled: member !== null,
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/contacts/{contact_id}/store-credit",
        {
          params: { path: { contact_id: member!.id } },
        },
      );
      if (!data) throw new Error(extractDetail(error) ?? "讀取餘額失敗");
      return data;
    },
  });

  const total = cartTotal(lines);
  const memberBalance =
    member !== null && balanceQuery.data
      ? (parseNtd(balanceQuery.data.balance) ?? 0)
      : null;
  const plan = resolvePlan(mode, total, parseNtd(cashInput) ?? 0);
  const validation = validatePlan(plan, total, {
    hasMember: member !== null,
    memberBalance,
  });

  const checkout = useMutation({
    mutationFn: async (): Promise<SaleRead> => {
      const { data, error } = await api.POST("/api/v1/sales", {
        params: { header: { "Idempotency-Key": idempotencyKey } },
        body: {
          lines: toSaleLines(lines),
          buyer_contact_id: member?.id ?? null,
          tenders: toTenders(plan) ?? null,
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "結帳失敗");
      return data;
    },
    onSuccess: (sale) => {
      setCompleted(sale);
      setShowDialog(true);
    },
  });

  function addToCart(line: CartLine) {
    const result = addLine(lines, line);
    setLines(result.lines);
    setNotice(
      result.duplicateSerialized
        ? `${line.description} 已在購物車（序號品不可重複）`
        : null,
    );
  }

  function resetSale() {
    setLines([]);
    setMember(null);
    setMode("CASH");
    setCashInput("");
    setReceivedInput("");
    setNotice(null);
    setCompleted(null);
    setShowDialog(false);
    setIdempotencyKey(crypto.randomUUID());
    checkout.reset();
  }

  // 完成畫面（結帳成功後）
  if (completed !== null) {
    return (
      <section>
        <h1 className="page-title">POS 結帳</h1>
        <div className="card pos-complete">
          <h2>
            已完成 <span className="badge-open">#{completed.id}</span>
          </h2>
          <dl className="stat-list">
            <div className="stat">
              <dt>總額</dt>
              <dd>
                <Money value={parseNtd(completed.total) ?? 0} />
              </dd>
            </div>
            <div className="stat">
              <dt>收款方式</dt>
              <dd>
                {completed.payment_method === "CASH"
                  ? "現金"
                  : completed.payment_method === "STORE_CREDIT"
                    ? "購物金"
                    : "混合"}
              </dd>
            </div>
          </dl>
          <div className="pos-dialog-actions">
            <button
              type="button"
              className="btn-ghost"
              onClick={() => setShowDialog(true)}
            >
              列印商品明細
            </button>
            <button type="button" className="btn-primary" onClick={resetSale}>
              開始下一筆
            </button>
          </div>
        </div>
        {showDialog && (
          <PrintDialog sale={completed} onClose={() => setShowDialog(false)} />
        )}
      </section>
    );
  }

  const einvoiceEnabled = settings.data?.einvoice_enabled ?? false;

  return (
    <section>
      <h1 className="page-title">POS 結帳</h1>
      <div className="pos-grid">
        <div className="pos-left">
          <ScanBar onResolved={addToCart} />
          {notice !== null && (
            <p role="alert" className="form-error">
              {notice}
            </p>
          )}
          {lines.length === 0 ? (
            <p className="pos-empty hint">掃描或輸入商品條碼開始結帳。</p>
          ) : (
            <table className="pos-cart">
              <thead>
                <tr>
                  <th>品項</th>
                  <th>單價</th>
                  <th>數量</th>
                  <th>小計</th>
                  <th aria-label="操作" />
                </tr>
              </thead>
              <tbody>
                {lines.map((line) => (
                  <tr key={line.key}>
                    <td>{line.description}</td>
                    <td>
                      <Money value={line.unitPrice} />
                    </td>
                    <td>
                      {line.lineType === "SERIALIZED" ? (
                        1
                      ) : (
                        <input
                          className="pos-qty"
                          inputMode="numeric"
                          value={line.qty}
                          aria-label={`${line.description} 數量`}
                          onChange={(e) =>
                            setLines(
                              setQty(
                                lines,
                                line.key,
                                parseNtd(e.target.value) ?? 1,
                              ),
                            )
                          }
                        />
                      )}
                    </td>
                    <td>
                      <Money value={lineTotal(line)} />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn-ghost"
                        aria-label={`移除 ${line.description}`}
                        onClick={() => setLines(removeLine(lines, line.key))}
                      >
                        移除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <aside className="pos-right card">
          <div className="pos-total">
            <span>應付總額</span>
            <strong>
              <Money value={total} />
            </strong>
          </div>

          <MemberPanel
            member={member}
            onSelect={setMember}
            onClear={() => setMember(null)}
          />

          <TenderPanel
            total={total}
            hasMember={member !== null}
            memberBalance={memberBalance}
            mode={mode}
            setMode={setMode}
            cashInput={cashInput}
            setCashInput={setCashInput}
            receivedInput={receivedInput}
            setReceivedInput={setReceivedInput}
          />

          {/* 發票區：未啟用電子發票時隱藏載具輸入，僅標示（docs/10 §5、§6 約束 §6 發票開關） */}
          {einvoiceEnabled ? (
            <label className="field">
              <span className="field-label">
                雲端發票載具（掃描手機條碼，8 碼 / 開頭）
              </span>
              <input name="carrier" inputMode="text" placeholder="/XXXXXXX" />
            </label>
          ) : (
            <p className="hint pos-invoice-off">
              本期不開票（未啟用電子發票）。
            </p>
          )}

          {checkout.isError && (
            <p role="alert" className="form-error">
              {(checkout.error as Error).message}
            </p>
          )}

          <button
            type="button"
            className="btn-primary pos-checkout"
            disabled={!validation.ok || checkout.isPending}
            onClick={() => checkout.mutate()}
          >
            {checkout.isPending ? "結帳中…" : "結帳"}
          </button>
        </aside>
      </div>
    </section>
  );
}
