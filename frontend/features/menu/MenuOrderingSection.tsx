"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type Item = Pick<components["schemas"]["MenuItemRead"], "id" | "name" | "sort_order">;
function message(error: unknown): string {
  return error && typeof error === "object" && "detail" in error && typeof error.detail === "string" ? error.detail : "儲存排序失敗";
}
export function MenuOrderingSection({ items, onChanged }: { items: Item[]; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const cache = useQueryClient();
  const categories = useQuery({
    queryKey: ["menu-categories"], enabled: open,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/menu-categories");
      if (!data) throw new Error(message(error));
      return data;
    },
  });
  return <details className="card menu-ordering" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>菜單排序</summary>
    {open && <>
      <p className="hint">數字小的排前面。排序會套用於 POS；線上菜單請再按「發佈到線上點餐」。售完商品會顯示在該分類後方。</p>
      <h3>分類順序</h3>
      {categories.isError && <p role="alert" className="form-error">{categories.error.message}</p>}
      {categories.isPending && <p role="status">讀取分類中…</p>}
      {(categories.data ?? []).map((category) => <OrderRow key={`${category.id}:${category.sort_order}`} item={category} kind="分類" save={async (sort_order) => {
        const { data, error } = await api.PATCH("/api/v1/menu-categories/{category_id}", { params: { path: { category_id: category.id } }, body: { sort_order } });
        if (!data) throw new Error(message(error));
        await cache.invalidateQueries({ queryKey: ["menu-categories"] });
      }} />)}
      <h3>商品順序</h3>
      {items.map((item) => <OrderRow key={`${item.id}:${item.sort_order}`} item={item} kind="商品" save={async (sort_order) => {
        const { data, error } = await api.PATCH("/api/v1/menu-items/{item_id}", { params: { path: { item_id: item.id } }, body: { sort_order } });
        if (!data) throw new Error(message(error));
        onChanged();
      }} />)}
    </>}
  </details>;
}
function OrderRow({ item, kind, save }: { item: Item; kind: "分類" | "商品"; save: (value: number) => Promise<void> }) {
  const [value, setValue] = useState(String(item.sort_order));
  const mutation = useMutation({ mutationFn: save });
  return <form className="menu-order-row" onSubmit={(event) => { event.preventDefault(); const n = Number(value); if (value.trim() && Number.isInteger(n) && n >= -2147483648 && n <= 2147483647) mutation.mutate(n); }}>
    <label><span>{item.name}</span><input type="number" step={1} min={-2147483648} max={2147483647} required aria-label={`${item.name} ${kind}排序`} value={value} disabled={mutation.isPending} onChange={(event) => setValue(event.target.value)} /></label>
    <button type="submit" className="btn-secondary" disabled={mutation.isPending} aria-label={`儲存${item.name}${kind}排序`}>{mutation.isPending ? "儲存中…" : "儲存"}</button>
    {mutation.isError && <p role="alert" className="form-error">{mutation.error.message}</p>}
    {mutation.isSuccess && <span role="status" className="hint">已儲存</span>}
  </form>;
}
