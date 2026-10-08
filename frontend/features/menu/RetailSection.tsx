"use client";
// 線上「帶回家」零售商品列表（菜單 →「線上發布」分頁；docs/63 §13、M1d）。新增與編輯各自一頁。
// 從現有一般商品挑上線，價格、庫存、分類都跟著原商品，這裡不另外填。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { apiDetail } from "@/features/menu/experienceOptions";
import { retailRoleLabel } from "@/features/menu/RetailForm";
import { api } from "@/lib/api";
import { formatNtd, parseNtd } from "@/lib/money";

export const RETAIL_KEY = ["online-retail"];

export function RetailSection() {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const listings = useQuery({
    queryKey: RETAIL_KEY,
    queryFn: async () => {
      const { data, error: e } = await api.GET("/api/v1/online-order/retail");
      if (!data) throw new Error(apiDetail(e, "讀取帶回家商品失敗"));
      return data;
    },
  });
  const remove = useMutation({
    mutationFn: async (id: number) => {
      const { error: e, response } = await api.DELETE("/api/v1/online-order/retail/{listing_id}", {
        params: { path: { listing_id: id } },
      });
      if (!response.ok) throw new Error(apiDetail(e, "下線失敗"));
    },
    onSuccess: () => {
      setConfirming(null);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: RETAIL_KEY });
    },
    onError: (reason: Error) => setError(reason.message),
  });

  return (
    <section className="card menu-experiences" aria-labelledby="menu-retail-title">
      <div className="menu-experiences-head">
        <div>
          <h2 id="menu-retail-title">帶回家（零售商品）</h2>
          <p className="hint">
            從現有商品挑上線上菜單，客人付款後到櫃檯領取。價格與庫存跟著原商品；改完到上方按「發佈到線上點餐」。
          </p>
        </div>
        <Link href="/menu/retail/new" className="btn-primary">新增帶回家商品</Link>
      </div>
      {listings.isError && <p role="alert" className="form-error">{listings.error.message}</p>}
      {error !== null && <p role="alert" className="form-error">{error}</p>}
      {listings.data && listings.data.length === 0 && <p className="hint">還沒有帶回家商品。</p>}
      <ul className="menu-experience-list">
        {(listings.data ?? []).map((row) => (
          <li key={row.id} className={`menu-experience-row${row.is_active ? "" : " is-off"}`}>
            <div className="menu-experience-text">
              <b>{row.product_name}</b>
              <span>
                ${formatNtd(parseNtd(row.unit_price) ?? 0)}・庫存 {row.quantity_on_hand}
                {row.category_name ? `・${row.category_name}` : ""}
              </span>
              <small>
                {row.role ? `加購：${retailRoleLabel(row.role)}` : "不參與加購"}
                {row.photo_sha256 ? "・有照片" : ""}
                {row.is_active ? "" : "・停用中"}
                {row.product_active ? "" : "・商品已停售"}
              </small>
            </div>
            <div className="menu-experience-actions">
              {confirming === row.id ? (
                <>
                  <button type="button" className="btn-danger" disabled={remove.isPending} onClick={() => remove.mutate(row.id)}>確定下線</button>
                  <button type="button" className="btn-ghost" onClick={() => setConfirming(null)}>取消</button>
                </>
              ) : (
                <>
                  <Link href={`/menu/retail/${row.id}`} className="btn-ghost">編輯</Link>
                  <button type="button" className="btn-ghost" onClick={() => setConfirming(row.id)}>下線</button>
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
