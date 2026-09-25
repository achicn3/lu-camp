"use client";
// 收購佇列的一列估價表單（新增與修改共用；docs/42 §4）。
// 原價 × 折數自動帶預計售價與建議收購價（與收購頁同一套計價）；成交收購價預設＝建議價、可改。
import { type FormEvent, useState } from "react";

import { GRADE_LABEL, SERIALIZED_GRADES } from "@/features/acquisition/labels";
import {
  type PricingRates,
  discountToPct,
  estimateLine,
  pctToDiscount,
} from "@/features/intake/estimate";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Line = components["schemas"]["IntakeLineRead"];
type AcqType = components["schemas"]["AcquisitionType"];
type Grade = components["schemas"]["Grade"];
export type LineFields = components["schemas"]["IntakeLineFields"];

const TYPE_OPTIONS: { value: AcqType; label: string }[] = [
  { value: "BUYOUT", label: "買斷" },
  { value: "CONSIGNMENT", label: "寄售" },
  { value: "BULK_LOT", label: "散裝" },
];
const QUICK_DISCOUNTS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

export function LineForm({
  initial,
  rates,
  defaultCommissionPct,
  submitLabel,
  busy,
  onSubmit,
  onCancel,
}: {
  initial?: Line;
  rates: PricingRates;
  defaultCommissionPct: number | null;
  submitLabel: string;
  busy: boolean;
  onSubmit: (fields: LineFields) => void;
  onCancel?: () => void;
}) {
  const [shortName, setShortName] = useState(initial?.short_name ?? "");
  const [qty, setQty] = useState(String(initial?.qty ?? 1));
  const [type, setType] = useState<AcqType>(initial?.acquisition_type ?? "BUYOUT");
  const [reference, setReference] = useState(initial?.reference_price ?? "");
  const [discount, setDiscount] = useState(pctToDiscount(initial?.discount_pct));
  const [customDiscount, setCustomDiscount] = useState(false);
  const [listed, setListed] = useState(initial?.expected_listed_price ?? "");
  const [dealCost, setDealCost] = useState(initial?.deal_cost ?? "");
  // 店員動過成交價就不再被建議價覆蓋（裁示 5：可改，建議與成交都留）。
  const [dealManual, setDealManual] = useState(initial?.deal_cost != null);
  const [commission, setCommission] = useState(
    initial?.commission_pct != null
      ? String(initial.commission_pct)
      : defaultCommissionPct === null
        ? ""
        : String(defaultCommissionPct),
  );
  const [grade, setGrade] = useState<Grade | "">(initial?.grade ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [error, setError] = useState<string | null>(null);

  const estimate = estimateLine(reference, discount, rates);
  const isConsignment = type === "CONSIGNMENT";

  function applyPricing(nextReference: string, nextDiscount: string) {
    const next = estimateLine(nextReference, nextDiscount, rates);
    if (next.listed !== null) setListed(String(next.listed));
    if (!dealManual && next.suggestedCost !== null) setDealCost(String(next.suggestedCost));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const count = parseNtd(qty);
    if (!shortName.trim()) return setError("請填商品簡稱（上架時才認得出是哪一件）");
    if (count === null || count < 1) return setError("數量至少 1");
    const pct = discount === "" ? null : discountToPct(discount);
    if (discount !== "" && pct === null) return setError("折數請填 0.1–10，最多一位小數");
    if (pct !== null && reference.trim() === "") return setError("用折數估價要先填原價");
    const money = (value: string) => (value.trim() === "" ? null : value.trim());
    for (const [label, value] of [["原價", reference], ["預計售價", listed], ["成交收購價", dealCost]]) {
      if (value.trim() !== "" && parseNtd(value) === null) return setError(`${label}請填整數元`);
    }
    if (isConsignment && commission.trim() === "") return setError("寄售要填抽成 %");
    onSubmit({
      short_name: shortName.trim(),
      qty: count,
      acquisition_type: type,
      reference_price: money(reference),
      discount_pct: pct,
      expected_listed_price: money(listed),
      suggested_cost: isConsignment || estimate.suggestedCost === null ? null : String(estimate.suggestedCost),
      deal_cost: isConsignment ? null : money(dealCost),
      commission_pct: isConsignment ? Number(commission) : null,
      // 成色沒點時依折數推斷（與收購頁同一支 gradeFromDiscount，裁示 10）。
      grade: grade === "" ? estimate.inferredGrade : grade,
      note: note.trim() || null,
    });
  }

  const subtotal = (parseNtd(dealCost) ?? 0) * (parseNtd(qty) ?? 0);

  return (
    <form className="intake-line-form" onSubmit={submit} aria-label={initial ? `修改第 ${initial.line_no} 列` : "新增一列"}>
      <div className="intake-line-grid">
        <label className="field intake-field-wide">
          <span className="field-label">商品簡稱</span>
          <input aria-label="商品簡稱" value={shortName} maxLength={100} placeholder="例如：黑色折疊椅"
            onChange={(e) => setShortName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">數量</span>
          <input aria-label="數量" inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">類型</span>
          <select aria-label="類型" value={type} onChange={(e) => setType(e.target.value as AcqType)}>
            {TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">原價／件</span>
          <input aria-label="原價／件" inputMode="numeric" value={reference}
            onChange={(e) => { setReference(e.target.value); applyPricing(e.target.value, discount); }} />
        </label>
      </div>

      <div className="acq-discounts" role="group" aria-label="預計售價折數">
        {QUICK_DISCOUNTS.map((n) => (
          <button key={n} type="button" className="btn-secondary"
            aria-pressed={!customDiscount && discount === n}
            onClick={() => { setCustomDiscount(false); setDiscount(n); applyPricing(reference, n); }}>
            {n}折
          </button>
        ))}
        <button type="button" className="btn-secondary" aria-pressed={customDiscount}
          onClick={() => setCustomDiscount(true)}>自訂</button>
        {customDiscount && (
          <input aria-label="自訂折數" inputMode="decimal" className="intake-custom-discount" value={discount}
            onChange={(e) => { setDiscount(e.target.value); applyPricing(reference, e.target.value); }} />
        )}
      </div>
      <p className="hint">五折＝預計賣原價的一半（不是用一半收購）；預計售價含稅與手續費、進位到十元。</p>
      {estimate.nearNew && (
        <p className="form-error acq-near-new" role="alert">
          {discount} 折偏高，可能是新品：請確認商品狀況並修改成色。
        </p>
      )}

      <div className="intake-line-grid">
        <label className="field">
          <span className="field-label">預計售價／件</span>
          <input aria-label="預計售價／件" inputMode="numeric" value={listed} onChange={(e) => setListed(e.target.value)} />
        </label>
        {isConsignment ? (
          <label className="field">
            <span className="field-label">寄售抽成 %</span>
            <input aria-label="寄售抽成 %" inputMode="numeric" value={commission} onChange={(e) => setCommission(e.target.value)} />
          </label>
        ) : (
          <label className="field">
            <span className="field-label">成交收購價／件</span>
            <input aria-label="成交收購價／件" inputMode="numeric" value={dealCost}
              onChange={(e) => { setDealCost(e.target.value); setDealManual(true); }} />
            {estimate.suggestedCost !== null && (
              <span className="intake-field-hint">建議 ${formatNtd(estimate.suggestedCost)}</span>
            )}
          </label>
        )}
        <label className="field">
          <span className="field-label">成色（選填）</span>
          <select aria-label="成色" value={grade} onChange={(e) => setGrade(e.target.value as Grade | "")}>
            <option value="">
              {estimate.inferredGrade ? `依折數：${GRADE_LABEL[estimate.inferredGrade]}` : "不點"}
            </option>
            {SERIALIZED_GRADES.map((g) => (
              <option key={g} value={g}>{GRADE_LABEL[g]}</option>
            ))}
          </select>
        </label>
        <label className="field intake-field-wide">
          <span className="field-label">配件／特殊狀況（選填）</span>
          <input aria-label="配件／特殊狀況" value={note} maxLength={500} placeholder="例如：缺收納袋"
            onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>

      {error !== null && <p role="alert" className="form-error">{error}</p>}
      <div className="intake-line-actions">
        {!isConsignment && subtotal > 0 && (
          <span className="hint">小計 <strong className="money">${formatNtd(subtotal)}</strong></span>
        )}
        {onCancel && <button type="button" className="btn-ghost" onClick={onCancel}>取消</button>}
        <button type="submit" className="btn-primary" disabled={busy}>{busy ? "儲存中…" : submitLabel}</button>
      </div>
    </form>
  );
}
