"use client";
// 排隊收購快速估價（docs/42 §13；店主 2026-10-02）：報到時照件數建好每一件，主畫面只填收購價。
// 平板上要很快：大輸入框、數字鍵盤，按 Enter／鍵盤的「下一個」就存這件並跳到下一件。
// 類型在每件直接點（二手／全新／散裝／寄售）；其他欄位（簡稱、原價、折數、售價、成色、分類、
// 品牌型號、備註）收在「詳細」裡，可填可不填，有填的上架時自動帶入。
import { useMutation } from "@tanstack/react-query";
import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import { LineForm, type LineFields } from "@/features/intake/LineForm";
import type { PricingRates } from "@/features/intake/estimate";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Batch = components["schemas"]["IntakeBatchRead"];
type Line = components["schemas"]["IntakeLineRead"];

const DELETABLE = new Set(["PENDING_ESTIMATE", "ESTIMATING"]);

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

/** 報到時自動取的名稱（「第 3 件」）不算真的名稱：畫面上已經有號碼，不重複顯示。 */
export function displayName(line: Pick<Line, "short_name" | "line_no">): string | null {
  return line.short_name === `第 ${line.line_no} 件` ? null : line.short_name;
}

function isPriced(line: Line): boolean {
  return line.acquisition_type === "CONSIGNMENT"
    ? line.commission_pct != null && line.expected_listed_price != null
    : line.deal_cost != null;
}

/** 估價時直接選的類型（店主 2026-10-02）：全新＝買斷、成色全新。 */
type Kind = "USED" | "NEW" | "BULK" | "CONSIGN";
const KINDS: { kind: Kind; label: string }[] = [
  { kind: "USED", label: "二手" },
  { kind: "NEW", label: "全新" },
  { kind: "BULK", label: "散裝" },
  { kind: "CONSIGN", label: "寄售" },
];

function kindOf(line: Line): Kind {
  if (line.acquisition_type === "CONSIGNMENT") return "CONSIGN";
  if (line.acquisition_type === "BULK_LOT") return "BULK";
  return line.grade === "N" ? "NEW" : "USED";
}

function kindFields(kind: Kind, line: Line): LineFields {
  switch (kind) {
    case "NEW":
      return { acquisition_type: "BUYOUT", grade: "N" };
    case "BULK":
      return { acquisition_type: "BULK_LOT", grade: null };
    case "CONSIGN":
      return { acquisition_type: "CONSIGNMENT", grade: line.grade ?? null };
    default:
      return { acquisition_type: "BUYOUT", grade: line.grade === "N" ? null : (line.grade ?? null) };
  }
}

type Field = "deal_cost" | "expected_listed_price" | "bulk_piece_count";

/** 一個隨時存檔的數字欄：按 Enter／鍵盤的「下一個」或離開欄位就存。
 *
 * 存檔還沒回來又改了：畫面保留新打的值、不被舊結果蓋掉，「估完」也擋住，直到新值存好
 * （Codex 對抗審查）。伺服器的值只有在沒改動、也沒在存的時候才帶回輸入框（例如「詳細」裡改了）。
 */
function SavedInput({
  batchId,
  lineId,
  field,
  saved,
  label,
  placeholder,
  prefix,
  suffix,
  narrow,
  onNext,
  onSaved,
  onUnsavedChange,
}: {
  batchId: number;
  lineId: number;
  field: Field;
  saved: string;
  label: string;
  placeholder: string;
  prefix?: string;
  suffix?: string;
  narrow?: boolean;
  onNext: (el: HTMLInputElement) => void;
  onSaved: () => void;
  onUnsavedChange: (key: string, unsaved: boolean) => void;
}) {
  const [value, setValue] = useState(saved);
  // 最後送出的值：按 Enter 存檔後焦點跳走會再觸發 blur，同一個值不重送；存檔失敗就退回，才能重試。
  // 事件裡用 ref（Enter 與跟著來的 blur 在同一輪，state 還沒更新）；畫面判斷用同步的 state。
  const sentRef = useRef(saved);
  const [sent, setSentState] = useState(saved);
  function setSent(text: string) {
    sentRef.current = text;
    setSentState(text);
  }
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = `${lineId}:${field}`;

  const patch = useMutation({
    mutationFn: async (body: LineFields) => {
      const { data, error: apiErr } = await api.PATCH("/api/v1/intake-batches/{batch_id}/lines/{line_id}", {
        params: { path: { batch_id: batchId, line_id: lineId } },
        body,
      });
      if (!data) throw new Error(detail(apiErr) ?? "儲存失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      setFailed(false);
      onSaved();
    },
    onError: (e: Error) => {
      setSent(saved); // 沒存進去：下次 Enter／離開欄位要重送
      setFailed(true);
      setError(e.message);
    },
  });

  // 伺服器的值變了（別處改的、或存好後重新讀取）：沒在改、沒在存才帶回來。
  const [seen, setSeen] = useState(saved);
  if (saved !== seen) {
    setSeen(saved);
    if (value.trim() === sent && !patch.isPending) {
      setSentState(saved);
      setValue(saved);
    }
  }
  useEffect(() => {
    sentRef.current = sent;
  }, [sent]);

  const unsaved = value.trim() !== saved || failed || patch.isPending;
  useEffect(() => {
    onUnsavedChange(key, unsaved);
  }, [key, unsaved, onUnsavedChange]);
  useEffect(() => () => onUnsavedChange(key, false), [key, onUnsavedChange]);

  /** 存這格（有改才送）。回傳 false＝格式不對，不跳下一格。 */
  function commit(): boolean {
    const text = value.trim();
    if (text === sentRef.current) return true;
    const n = parseNtd(text);
    if (text !== "" && (n === null || !/^\d+$/.test(text) || (field === "bulk_piece_count" && n < 1))) {
      setError(field === "bulk_piece_count" ? "件數請填正整數" : `${placeholder}請填整數元`);
      return false;
    }
    setError(null);
    setSent(text);
    if (field === "bulk_piece_count") patch.mutate({ bulk_piece_count: text === "" ? null : n });
    else patch.mutate({ [field]: text === "" ? null : String(n) });
    return true;
  }

  return (
    <div className={`intake-quick-field${narrow ? " is-narrow" : ""}`}>
      <label className="intake-quick-price">
        {prefix && <span aria-hidden="true">{prefix}</span>}
        <input
          data-quick-input=""
          aria-label={label}
          inputMode="numeric"
          enterKeyHint="next"
          autoComplete="off"
          placeholder={placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
            if (e.key !== "Enter") return;
            e.preventDefault();
            if (commit()) onNext(e.currentTarget);
          }}
          onBlur={() => commit()}
        />
        {suffix && <span aria-hidden="true">{suffix}</span>}
      </label>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </div>
  );
}

function PriceRow({
  batchId,
  line,
  onNext,
  onSaved,
  rates,
  defaultCommissionPct,
  deletable,
  onUnsavedChange,
}: {
  batchId: number;
  line: Line;
  onNext: (el: HTMLInputElement) => void;
  onSaved: () => void;
  rates: PricingRates;
  defaultCommissionPct: number | null;
  deletable: boolean;
  /** 這件的數字改了還沒存好（含存檔失敗、存檔中）：上層據此擋住「估完」，不讓舊價格成交。 */
  onUnsavedChange: (key: string, unsaved: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = `${line.line_no} 號`;
  const name = displayName(line);
  const kind = kindOf(line);

  const patch = useMutation({
    mutationFn: async (fields: LineFields) => {
      const { data, error: apiErr } = await api.PATCH("/api/v1/intake-batches/{batch_id}/lines/{line_id}", {
        params: { path: { batch_id: batchId, line_id: line.id } },
        body: fields,
      });
      if (!data) throw new Error(detail(apiErr) ?? "儲存失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      setOpen(false);
      onSaved();
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: async () => {
      const { error: apiErr, response } = await api.DELETE(
        "/api/v1/intake-batches/{batch_id}/lines/{line_id}",
        { params: { path: { batch_id: batchId, line_id: line.id } } },
      );
      if (!response.ok) throw new Error(detail(apiErr) ?? "刪除失敗");
    },
    onSuccess: onSaved,
    onError: (e: Error) => setError(e.message),
  });

  const common = { batchId, lineId: line.id, onNext, onSaved, onUnsavedChange };
  return (
    <li className={`intake-quick-row${isPriced(line) ? " is-priced" : ""}`}>
      <div className="intake-quick-main">
        <span className="intake-quick-no">{label}</span>
        <span className="intake-quick-name">
          {name ?? <span className="hint">（未命名，可在詳細填）</span>}
          {line.acquisition_type === "BULK_LOT" && line.qty > 1 && <span className="row-sub">×{line.qty}</span>}
        </span>
        <button
          type="button"
          className="btn-ghost intake-quick-toggle"
          aria-expanded={open}
          aria-label={`${label} 詳細`}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "收起" : "詳細"}
        </button>
        {deletable && (
          <button
            type="button"
            className="btn-ghost btn-danger-text"
            aria-label={`刪除 ${label}`}
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            刪除
          </button>
        )}
      </div>
      <div className="intake-quick-body">
        <div className="intake-quick-types" role="group" aria-label={`${label} 類型`}>
          {KINDS.map((k) => (
            <button
              key={k.kind}
              type="button"
              aria-pressed={kind === k.kind}
              className={kind === k.kind ? "is-on" : undefined}
              disabled={patch.isPending}
              onClick={() => {
                if (k.kind !== kind) patch.mutate(kindFields(k.kind, line));
              }}
            >
              {k.label}
            </button>
          ))}
        </div>
        <div className="intake-quick-inputs">
          {kind === "CONSIGN" ? (
            <SavedInput
              key="expected_listed_price"
              {...common}
              field="expected_listed_price"
              saved={line.expected_listed_price ?? ""}
              label={`${label} 寄售售價`}
              placeholder="寄售售價"
              prefix="$"
            />
          ) : kind === "BULK" ? (
            <>
              <SavedInput
                key="deal_cost"
                {...common}
                field="deal_cost"
                saved={line.deal_cost ?? ""}
                label={`${label} 整堆總價`}
                placeholder="整堆總價"
                prefix="$"
              />
              {line.qty === 1 && (
                <SavedInput
                  key="bulk_piece_count"
                  {...common}
                  field="bulk_piece_count"
                  saved={line.bulk_piece_count == null ? "" : String(line.bulk_piece_count)}
                  label={`${label} 件數（可不填）`}
                  placeholder="件數"
                  prefix="共"
                  suffix="件"
                  narrow
                />
              )}
            </>
          ) : (
            <SavedInput
              key="deal_cost"
              {...common}
              field="deal_cost"
              saved={line.deal_cost ?? ""}
              label={`${label} 收購價`}
              placeholder="收購價"
              prefix="$"
            />
          )}
        </div>
      </div>
      {kind === "BULK" && line.deal_cost != null && (
        <p className="intake-quick-consign">
          {line.bulk_piece_count
            ? `整堆 $${formatNtd(parseNtd(line.deal_cost) ?? 0)}，共 ${line.bulk_piece_count} 件，每件約 $${((parseNtd(line.deal_cost) ?? 0) / line.bulk_piece_count).toLocaleString("en-US", {
                maximumFractionDigits: 1,
              })}`
            : `整堆 $${formatNtd(parseNtd(line.deal_cost) ?? 0)}（沒填件數＝整堆算 1 件）`}
        </p>
      )}
      {kind === "CONSIGN" && (
        <p className="intake-quick-consign">寄售：賣出後分帳，抽成 {line.commission_pct ?? "—"}%（要改請按「詳細」）</p>
      )}
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {open && (
        <LineForm
          initial={line}
          rates={rates}
          defaultCommissionPct={defaultCommissionPct}
          submitLabel="儲存詳細"
          busy={patch.isPending}
          quick
          onSubmit={(fields) => patch.mutate(fields)}
          onCancel={() => setOpen(false)}
        />
      )}
    </li>
  );
}

export function QuickEstimate({
  batch,
  rates,
  defaultCommissionPct,
  onChanged,
  finish,
}: {
  batch: Batch;
  rates: PricingRates;
  defaultCommissionPct: number | null;
  onChanged: () => void;
  /** 估完後回來改價（編輯模式）：最下面的按鈕改成「回到客人確認」，不再送估完。 */
  finish?: { label: string; onClick: () => void };
}) {
  const list = useRef<HTMLOListElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [unsaved, setUnsaved] = useState<Set<string>>(new Set());
  const onUnsavedChange = useCallback((key: string, isUnsaved: boolean) => {
    setUnsaved((prev) => {
      if (prev.has(key) === isUnsaved) return prev;
      const next = new Set(prev);
      if (isUnsaved) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);
  const lines = batch.lines;
  const priced = lines.filter(isPriced).reduce((n, l) => n + l.qty, 0);
  const total = lines.reduce((n, l) => n + l.qty, 0);
  const sum = lines.reduce(
    (n, l) => n + (l.acquisition_type === "CONSIGNMENT" ? 0 : (parseNtd(l.deal_cost ?? "") ?? 0) * l.qty),
    0,
  );
  const allPriced = lines.length > 0 && lines.every(isPriced);

  const add = useMutation({
    mutationFn: async () => {
      const next = Math.max(0, ...lines.map((l) => l.line_no)) + 1;
      const { data, error: apiErr } = await api.POST("/api/v1/intake-batches/{batch_id}/lines", {
        params: { path: { batch_id: batch.id } },
        body: { short_name: `第 ${next} 件`, qty: 1, acquisition_type: "BUYOUT" },
      });
      if (!data) throw new Error(detail(apiErr) ?? "新增失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: Error) => setError(e.message),
  });

  const ready = useMutation({
    mutationFn: async () => {
      const { data, error: apiErr } = await api.POST("/api/v1/intake-batches/{batch_id}/ready", {
        params: { path: { batch_id: batch.id } },
      });
      if (!data) throw new Error(detail(apiErr) ?? "送出失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: Error) => setError(e.message),
  });

  /** 跳到下一格（下一件的收購價；散裝的總價之後是件數）。最後一格就收起鍵盤。 */
  function focusAfter(current: HTMLInputElement) {
    const all = Array.from(list.current?.querySelectorAll<HTMLInputElement>("input[data-quick-input]") ?? []);
    const next = all[all.indexOf(current) + 1];
    if (next) {
      next.focus();
      next.select();
    } else {
      current.blur();
    }
  }

  return (
    <div className="card intake-quick">
      <div className="intake-quick-head">
        <h2>估價</h2>
        <span className="intake-quick-progress" role="status">
          已填 {priced}／{total} 件・收購價合計 <strong className="money">${formatNtd(sum)}</strong>
        </span>
      </div>
      <p className="hint">
        每件先點類型（預設二手），再填收購價，按「下一個」跳到下一件。散裝填整堆總價，件數可不填；寄售填寄售售價。要記名稱、成色、分類、品牌等可以按「詳細」，不填也可以——上架時再補。
      </p>
      <ol className="intake-quick-list" ref={list}>
        {lines.map((line) => (
          <PriceRow
            key={line.id}
            batchId={batch.id}
            line={line}
            onNext={focusAfter}
            onSaved={onChanged}
            rates={rates}
            defaultCommissionPct={defaultCommissionPct}
            deletable={DELETABLE.has(batch.status) && lines.length > 1}
            onUnsavedChange={onUnsavedChange}
          />
        ))}
      </ol>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {unsaved.size > 0 && (
        <p className="hint intake-quick-unsaved">還有價格沒存好：按 Enter 存檔（存檔中請稍等；失敗的再按一次）。</p>
      )}
      <div className="intake-quick-actions">
        <button type="button" className="btn-secondary" disabled={add.isPending} onClick={() => add.mutate()}>
          ＋ 多一件
        </button>
        {finish ? (
          <button
            type="button"
            className="btn-primary intake-quick-ready"
            disabled={unsaved.size > 0}
            onClick={finish.onClick}
          >
            {finish.label}
          </button>
        ) : (
          <button
            type="button"
            className="btn-primary intake-quick-ready"
            disabled={!allPriced || unsaved.size > 0 || ready.isPending}
            onClick={() => ready.mutate()}
          >
            估完，給客人確認
          </button>
        )}
      </div>
    </div>
  );
}
