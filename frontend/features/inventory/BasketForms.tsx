"use client";
// 販售籃從現有散裝開籃／加入（店主 2026-10-10：豬尾巴後來想共用標籤，不必再透過收購頁）。限管理者。
// 開新籃：名稱、品牌、分類、每件售價帶第一筆勾的散裝（可改名、改價）；後端一次建好並放進去，
// 任一筆加不進去整筆不成立。加入現有：逐筆加入，同價才能加（2026-09-22 裁示）。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { BasketLotPicker, type BulkLot } from "@/features/inventory/BasketLotPicker";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { parseNtd } from "@/lib/money";

type BulkBasket = components["schemas"]["BulkBasketRead"];

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const value = (error as { detail: unknown }).detail;
    if (typeof value === "string") return value;
  }
  return null;
}

function toggled(list: BulkLot[], lot: BulkLot): BulkLot[] {
  return list.some((l) => l.id === lot.id) ? list.filter((l) => l.id !== lot.id) : [...list, lot];
}

export function NewBasketForm({
  onDone,
  onCancel,
}: {
  onDone: (message: string) => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const [lots, setLots] = useState<BulkLot[]>([]);
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");
  const [error, setError] = useState<string | null>(null);
  const first = lots[0] ?? null;
  const priceValue = parseNtd(price);

  function toggle(lot: BulkLot) {
    const next = toggled(lots, lot);
    // 第一筆勾下去：名稱與售價帶它的（店員可以再改）。
    if (lots.length === 0 && next.length === 1) {
      setName(lot.name);
      setPrice(String(parseNtd(lot.unit_price) ?? ""));
    }
    setLots(next);
    setError(null);
  }

  const create = useMutation({
    mutationFn: async () => {
      if (first === null) throw new Error("請至少勾一筆散裝");
      if (!name.trim()) throw new Error("請填販售籃名稱");
      if (priceValue === null || priceValue <= 0) throw new Error("每件售價須為正整數元");
      const { data, error: apiErr } = await api.POST("/api/v1/bulk-baskets", {
        body: {
          name: name.trim(),
          unit_price: String(priceValue),
          brand_id: first.brand_id,
          category_id: first.category_id,
          bulk_lot_ids: lots.map((lot) => lot.id),
        },
      });
      if (!data) throw new Error(detail(apiErr) ?? "建立販售籃失敗");
      return data;
    },
    onSuccess: (basket) => {
      void queryClient.invalidateQueries({ queryKey: ["bulk-baskets"] });
      void queryClient.invalidateQueries({ queryKey: ["bulk-lots"] });
      onDone(
        `已開販售籃「${basket.name}」（${lots.length} 筆散裝、共 ${basket.remaining_qty} 件），` +
          "請印籃子標籤貼上；舊的散裝標籤掃了也會算這一籃。",
      );
    },
    onError: (e: Error) => setError(e.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <form className="card inv-basket-form" aria-label="開新販售籃" onSubmit={submit}>
      <h3>開新販售籃</h3>
      <p className="hint">找出要共用一張標籤的散裝勾起來；名稱和售價先帶第一筆的，可以改。</p>
      <BasketLotPicker requiredPrice={priceValue} selected={lots} onToggle={toggle} />
      <div className="inv-filters">
        <label className="field">
          <span className="field-label">販售籃名稱</span>
          <input aria-label="販售籃名稱" value={name} maxLength={150} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">每件售價</span>
          <input
            aria-label="每件售價"
            inputMode="numeric"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
          />
        </label>
      </div>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <div className="inv-actions">
        <button type="submit" className="btn-primary" disabled={lots.length === 0 || create.isPending}>
          建立販售籃
        </button>
        <button type="button" className="btn-ghost" onClick={onCancel}>
          取消
        </button>
      </div>
    </form>
  );
}

export function AddLotsForm({
  basket,
  onDone,
}: {
  basket: BulkBasket;
  onDone: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [lots, setLots] = useState<BulkLot[]>([]);
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: async () => {
      // 逐筆加入：每筆各自成立；中途被擋就停在那一筆並講原因（前面加好的保留）。
      for (const [index, lot] of lots.entries()) {
        const { data, error: apiErr } = await api.POST("/api/v1/bulk-baskets/{basket_id}/lots", {
          params: { path: { basket_id: basket.id } },
          body: { bulk_lot_id: lot.id },
        });
        if (!data) {
          throw new Error(
            `${lot.lot_code} 加不進去：${detail(apiErr) ?? "加入失敗"}` +
              (index > 0 ? `（前 ${index} 筆已加入）` : ""),
          );
        }
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["bulk-baskets"] });
      void queryClient.invalidateQueries({ queryKey: ["bulk-lots"] });
      onDone(`已把 ${lots.length} 筆散裝加入「${basket.name}」；舊的散裝標籤掃了也會算這一籃。`);
    },
    onError: (e: Error) => {
      void queryClient.invalidateQueries({ queryKey: ["bulk-baskets"] });
      setError(e.message);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    add.mutate();
  }

  return (
    <form className="inv-basket-form" aria-label={`${basket.name} 加入現有散裝`} onSubmit={submit}>
      <BasketLotPicker
        requiredPrice={parseNtd(basket.unit_price)}
        selected={lots}
        onToggle={(lot) => {
          setLots((prev) => toggled(prev, lot));
          setError(null);
        }}
      />
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <button type="submit" className="btn-primary" disabled={lots.length === 0 || add.isPending}>
        加入這 {lots.length} 筆
      </button>
    </form>
  );
}
