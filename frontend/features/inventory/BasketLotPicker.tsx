"use client";
// 販售籃挑現有散裝（店主 2026-10-10）：搜尋散裝、勾要放進籃子的幾筆。
// 已在別的籃、收購已作廢的不列；售價跟籃子不同的不能勾（同價才能放同一籃，2026-09-22 裁示）。
import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

export type BulkLot = components["schemas"]["BulkLotRead"];

function price(lot: BulkLot): number {
  return parseNtd(lot.unit_price) ?? 0;
}

export function BasketLotPicker({
  requiredPrice,
  selected,
  onToggle,
}: {
  /** 籃子的每件售價；null＝還沒定（開新籃、還沒勾任何一筆時）。 */
  requiredPrice: number | null;
  selected: BulkLot[];
  onToggle: (lot: BulkLot) => void;
}) {
  const [input, setInput] = useState("");
  const [q, setQ] = useState<string | null>(null);
  const lots = useQuery({
    queryKey: ["bulk-lots", "basket-picker", q],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/bulk-lots", {
        params: { query: { q: q ?? undefined, limit: 50 } },
      });
      return (data ?? []).filter((lot) => lot.basket_id == null && lot.status !== "WRITTEN_OFF");
    },
    enabled: q !== null,
  });

  function search(event: FormEvent) {
    event.preventDefault();
    setQ(input.trim());
  }

  const selectedIds = new Set(selected.map((lot) => lot.id));
  return (
    <div className="inv-basket-picker">
      <div className="inv-filters">
        <input
          aria-label="搜尋散裝"
          className="inv-search"
          placeholder="散裝名稱或編號，例如：豬尾巴"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") search(e);
          }}
        />
        <button type="button" className="btn-secondary" onClick={search}>
          找散裝
        </button>
      </div>
      {lots.isFetching && <p className="hint">搜尋中…</p>}
      {lots.isSuccess && lots.data.length === 0 && (
        <p className="hint">沒有可以放進籃子的散裝（已在別的籃或收購已作廢的不列）。</p>
      )}
      {lots.isSuccess && lots.data.length > 0 && (
        <ul className="inv-basket-picker-list">
          {lots.data.map((lot) => {
            const checked = selectedIds.has(lot.id);
            const mismatch = requiredPrice !== null && price(lot) !== requiredPrice;
            return (
              <li key={lot.id}>
                <label className={mismatch && !checked ? "inv-row-muted" : undefined}>
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={mismatch && !checked}
                    onChange={() => onToggle(lot)}
                  />
                  <span className="mono">{lot.lot_code}</span> {lot.name}・剩 {lot.remaining_qty} 件・每件{" "}
                  {formatNtd(price(lot))} 元
                </label>
                {mismatch && !checked && (
                  <span className="hint">
                    售價 {formatNtd(price(lot))} 元不同；同價才能放同一籃，要放請先把售價改成一樣
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
