"use client";

// 一般商品手動改數量（店主 2026-10-24）：編輯視窗內一區，獨立送出。
// 庫存價值＝數量 × 最新進價，數量改了成本就跟著變；不另記損耗支出。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import {
  QUANTITY_ADJUST_REASONS,
  checkQuantityAdjust,
  extractDetail,
} from "@/features/inventory/inventory";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type Reason = components["schemas"]["CatalogQuantityAdjustReason"];

export function CatalogQuantityAdjust({
  productId,
  quantity,
  disabled,
  onDone,
}: {
  productId: number;
  quantity: number;
  disabled: boolean;
  onDone: () => void;
}) {
  const [nextQty, setNextQty] = useState(String(quantity));
  const [reason, setReason] = useState<Reason>("COUNT_CORRECTION");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const adjust = useMutation({
    mutationFn: async () => {
      const checked = checkQuantityAdjust(quantity, nextQty, reason);
      if ("error" in checked) throw new Error(checked.error);
      const { data, error: e } = await api.POST(
        "/api/v1/catalog-products/{product_id}/quantity-adjustments",
        {
          params: { path: { product_id: productId } },
          body: {
            expected_qty: quantity,
            new_qty: checked.newQty,
            reason,
            note: note.trim() || null,
          },
        },
      );
      if (!data) throw new Error(extractDetail(e) ?? "更新數量失敗");
    },
    onSuccess: () => {
      setError(null);
      onDone();
    },
    onError: (err: Error) => setError(err.message),
  });
  const busy = disabled || adjust.isPending;

  return (
    <>
      <p className="inv-edit-section">數量</p>
      <p className="hint">
        目前 <strong>{quantity}</strong> 件。改了數量，庫存價值（數量 × 最新進價）會跟著變；異動與原因會記在明細歷史。
      </p>
      <label className="field">
        <span className="field-label">新數量</span>
        <input
          inputMode="numeric"
          aria-label="新數量"
          value={nextQty}
          disabled={busy}
          onChange={(e) => setNextQty(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">原因</span>
        <select
          aria-label="調整原因"
          value={reason}
          disabled={busy}
          onChange={(e) => setReason(e.target.value as Reason)}
        >
          {(Object.keys(QUANTITY_ADJUST_REASONS) as Reason[]).map((key) => (
            <option key={key} value={key}>
              {QUANTITY_ADJUST_REASONS[key].label}
              {QUANTITY_ADJUST_REASONS[key].decreaseOnly ? "（只能減少）" : ""}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field-label">備註（選填，勿填個資）</span>
        <input
          aria-label="調整備註"
          value={note}
          maxLength={200}
          disabled={busy}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <button type="button" className="btn-ghost" disabled={busy} onClick={() => adjust.mutate()}>
        {adjust.isPending ? "更新中…" : "更新數量"}
      </button>
    </>
  );
}
