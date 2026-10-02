"use client";
// 排隊收購快速估價（docs/42 §13；店主 2026-10-02）：報到時照件數建好每一件，主畫面只填收購價。
// 平板上要很快：大輸入框、數字鍵盤，按 Enter／鍵盤的「下一個」就存這件並跳到下一件。
// 其他欄位（簡稱、類型、原價、折數、售價、成色、分類、品牌型號、備註）收在「詳細」裡，可填可不填，
// 有填的上架時自動帶入。
import { useMutation } from "@tanstack/react-query";
import { type KeyboardEvent, useRef, useState } from "react";

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
  return line.acquisition_type === "CONSIGNMENT" ? line.commission_pct != null : line.deal_cost != null;
}

function PriceRow({
  batchId,
  line,
  inputRef,
  onNext,
  onSaved,
  rates,
  defaultCommissionPct,
  deletable,
}: {
  batchId: number;
  line: Line;
  inputRef: (el: HTMLInputElement | null) => void;
  onNext: () => void;
  onSaved: () => void;
  rates: PricingRates;
  defaultCommissionPct: number | null;
  deletable: boolean;
}) {
  const saved = line.deal_cost ?? "";
  const [value, setValue] = useState(saved);
  // 最後送出的值：按 Enter 存檔後焦點跳走會再觸發 blur，同一個值不重送。
  const sent = useRef(saved);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = `${line.line_no} 號`;
  const name = displayName(line);

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

  /** 存這件（有改才送）。回傳 false＝格式不對，不跳下一件。 */
  function commit(): boolean {
    const text = value.trim();
    if (text === sent.current) return true;
    const price = parseNtd(text);
    if (text !== "" && (price === null || price < 0 || !/^\d+$/.test(text))) {
      setError("收購價請填整數元");
      return false;
    }
    setError(null);
    sent.current = text;
    patch.mutate({ deal_cost: text === "" ? null : String(price) });
    return true;
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (commit()) onNext();
  }

  const consignment = line.acquisition_type === "CONSIGNMENT";
  return (
    <li className={`intake-quick-row${isPriced(line) ? " is-priced" : ""}`}>
      <div className="intake-quick-main">
        <span className="intake-quick-no">{label}</span>
        <span className="intake-quick-name">
          {name ?? <span className="hint">（未命名，可在詳細填）</span>}
          {line.acquisition_type === "BULK_LOT" && <span className="row-sub">散裝 ×{line.qty}</span>}
        </span>
        {consignment ? (
          <span className="intake-quick-consign">
            寄售・售價 ${formatNtd(parseNtd(line.expected_listed_price ?? "") ?? 0)}・抽成{" "}
            {line.commission_pct ?? "—"}%
          </span>
        ) : (
          <label className="intake-quick-price">
            <span aria-hidden="true">$</span>
            <input
              ref={inputRef}
              aria-label={`${label} 收購價`}
              inputMode="numeric"
              enterKeyHint="next"
              autoComplete="off"
              placeholder="收購價"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={onKeyDown}
              onBlur={() => commit()}
            />
          </label>
        )}
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
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  const [error, setError] = useState<string | null>(null);
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

  function focusAfter(index: number) {
    for (let i = index + 1; i < inputs.current.length; i++) {
      const el = inputs.current[i];
      if (el) {
        el.focus();
        el.select();
        return;
      }
    }
    (document.activeElement as HTMLElement | null)?.blur();
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
        每件只要填收購價，按「下一個」跳到下一件。要記名稱、成色、分類、品牌等可以按「詳細」，不填也可以——上架時再補。
      </p>
      <ol className="intake-quick-list">
        {lines.map((line, i) => (
          <PriceRow
            key={`${line.id}-${line.deal_cost ?? ""}`}
            batchId={batch.id}
            line={line}
            inputRef={(el) => {
              inputs.current[i] = el;
            }}
            onNext={() => focusAfter(i)}
            onSaved={onChanged}
            rates={rates}
            defaultCommissionPct={defaultCommissionPct}
            deletable={DELETABLE.has(batch.status) && lines.length > 1}
          />
        ))}
      </ol>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <div className="intake-quick-actions">
        <button type="button" className="btn-secondary" disabled={add.isPending} onClick={() => add.mutate()}>
          ＋ 多一件
        </button>
        {finish ? (
          <button type="button" className="btn-primary intake-quick-ready" onClick={finish.onClick}>
            {finish.label}
          </button>
        ) : (
          <button
            type="button"
            className="btn-primary intake-quick-ready"
            disabled={!allPriced || ready.isPending}
            onClick={() => ready.mutate()}
          >
            估完，給客人確認
          </button>
        )}
      </div>
    </div>
  );
}
