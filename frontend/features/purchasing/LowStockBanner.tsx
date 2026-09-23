"use client";
// 低庫存提醒（採購單列表頂端的一條提示）：沒有低庫存就不佔版面；有的話列出來，
// 可單項或一次全部帶入「建立採購單」。在途已足（現量＋待到貨 ≥ 補貨點）的不列入「全部帶入」，
// 避免重複下單。
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { extractDetail } from "@/features/purchasing/shared";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type CatalogProduct = components["schemas"]["CatalogProductRead"];

/** 還缺幾件才到補貨點：扣掉現量與在途（至少 1）。在途只補一部分時，不重複下單。 */
export function shortage(product: CatalogProduct): number {
  return Math.max(1, product.reorder_point - product.quantity_on_hand - product.incoming_qty);
}

/** 帶進建立頁的網址：reorder=商品id:建議數量,…（清單才有在途數量，單查一項商品沒有）。 */
export function reorderHref(products: CatalogProduct[]): string {
  return `/purchasing/new?reorder=${products.map((p) => `${p.id}:${shortage(p)}`).join(",")}`;
}

export function LowStockBanner() {
  const [open, setOpen] = useState(false);
  const lowStock = useQuery({
    queryKey: ["catalog-products", "low-stock"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/catalog-products", {
        params: { query: { low_stock: true, limit: 100, offset: 0 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取低庫存清單失敗");
      return data;
    },
  });

  if (lowStock.isError) {
    return (
      <p role="alert" className="form-error">
        {lowStock.error.message}
      </p>
    );
  }
  const rows = lowStock.data ?? [];
  if (rows.length === 0) return null;
  const needed = rows.filter((p) => p.quantity_on_hand + p.incoming_qty < p.reorder_point);

  return (
    <div className="card pur-lowstock-banner" role="region" aria-label="低庫存提醒">
      <div className="pur-lowstock-banner-head">
        <p>
          <strong>{rows.length} 項</strong>低於補貨點
          {needed.length < rows.length && (
            <span className="row-sub">（其中 {rows.length - needed.length} 項已在途）</span>
          )}
        </p>
        <div className="pur-lowstock-banner-actions">
          <button
            type="button"
            className="btn-ghost"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? "收起" : "查看"}
          </button>
          {needed.length > 0 && (
            <Link href={reorderHref(needed)} className="btn-primary">
              全部帶入建立採購單
            </Link>
          )}
        </div>
      </div>
      {open && (
        <ul className="pur-lowstock-list">
          {rows.map((p) => {
            const covered = p.quantity_on_hand + p.incoming_qty >= p.reorder_point;
            return (
              <li key={p.id}>
                <span className="pur-lowstock-name">{p.name}</span>
                <span className="pur-lowstock-qty">
                  現量 {p.quantity_on_hand} / 補貨點 {p.reorder_point}
                  {p.incoming_qty > 0 && (
                    <span className="pur-incoming">
                      ・待到貨 {p.incoming_qty}
                      {covered && <span className="pur-covered">（在途已足）</span>}
                    </span>
                  )}
                </span>
                <Link
                  href={reorderHref([p])}
                  className="btn-secondary pur-reorder-btn"
                  aria-label={`補貨 ${p.name}`}
                >
                  補貨 →
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
