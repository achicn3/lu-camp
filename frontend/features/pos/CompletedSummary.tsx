// 結帳完成頁的補充資訊（店主 2026-10-04）：現金實收／找零，與帶備註商品的品牌、品名、備註。
// 客人付完錢店員才去拿貨——完成頁要讓店員一眼看到該找多少、哪幾件要特別交代（備註後的
// 「-末三碼」是包裝的位置）。
"use client";

import { useQueries } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { formatNtd } from "@/lib/money";

export interface CompletedNotedItem {
  key: string;
  description: string;
  /** 已接上「-條碼末三碼」的備註。 */
  note: string;
  brandId?: number | null;
}

export function CompletedCashChange({ received, change }: { received: number; change: number }) {
  return (
    <dl role="group" aria-label="現金找零" className="stat-list pos-complete-cash">
      <div className="stat">
        <dt>實收現金</dt>
        <dd>${formatNtd(received)}</dd>
      </div>
      <div className="stat pos-complete-change">
        <dt>找零</dt>
        <dd>${formatNtd(change)}</dd>
      </div>
    </dl>
  );
}

export function CompletedNotedItems({ items }: { items: CompletedNotedItem[] }) {
  const brandIds = [
    ...new Set(items.flatMap((item) => (item.brandId != null ? [item.brandId] : []))),
  ];
  const brands = useQueries({
    queries: brandIds.map((brandId) => ({
      queryKey: ["brand", brandId],
      queryFn: async () => {
        const { data, error } = await api.GET("/api/v1/brands/{brand_id}", {
          params: { path: { brand_id: brandId } },
        });
        if (!data) throw new Error(error ? "讀取品牌失敗" : "找不到品牌");
        return data;
      },
      staleTime: Infinity,
    })),
  });
  const brandName = new Map(
    brands.flatMap((query) => (query.data ? [[query.data.id, query.data.name] as const] : [])),
  );
  if (items.length === 0) return null;
  return (
    <section aria-label="帶備註的商品" className="pos-complete-notes">
      <h3>帶備註的商品（交貨前確認）</h3>
      <ul>
        {items.map((item) => {
          const brand = item.brandId != null ? brandName.get(item.brandId) : undefined;
          return (
            <li key={item.key}>
              {brand !== undefined && <span className="pos-complete-note-brand">{brand}</span>}
              <span className="pos-complete-note-name">{item.description}</span>
              <span className="pos-complete-note-body">{item.note}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
