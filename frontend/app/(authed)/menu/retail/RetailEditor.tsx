"use client";
// 帶回家商品新增／編輯頁的共用外框。新增存好轉到編輯頁（可以加照片）；編輯存好或取消回「線上發布」分頁。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { apiDetail } from "@/features/menu/experienceOptions";
import { RetailForm } from "@/features/menu/RetailForm";
import { RETAIL_KEY } from "@/features/menu/RetailSection";
import { api } from "@/lib/api";

const BACK = "/menu?section=online";

export function RetailEditor({ listingId }: { listingId: number | null }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const listings = useQuery({
    queryKey: RETAIL_KEY,
    enabled: listingId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/online-order/retail");
      if (!data) throw new Error(apiDetail(error, "讀取帶著走商品失敗"));
      return data;
    },
  });
  const initial = listingId === null ? null : listings.data?.find((row) => row.id === listingId);
  const loading = listingId !== null && listings.isPending;

  return (
    <section className="exp-page">
      <Link href={BACK} className="pur-back">← 回線上發布</Link>
      <h1 className="page-title">{listingId === null ? "新增帶著走商品" : "編輯帶著走商品"}</h1>
      {loading && <p role="status">載入中…</p>}
      {listings.error && <p role="alert" className="form-error">{listings.error.message}</p>}
      {!loading && !listings.error && listingId !== null && initial === undefined && (
        <p role="alert" className="form-error">找不到這個帶著走商品，可能已經下線了。</p>
      )}
      {!loading && !listings.error && initial !== undefined && (
        <RetailForm
          key={initial?.id ?? "new"}
          initial={initial}
          onDone={(saved) => {
            void queryClient.invalidateQueries({ queryKey: RETAIL_KEY });
            router.push(listingId === null ? `/menu/retail/${saved.id}` : BACK);
          }}
          onCancel={() => router.push(BACK)}
        />
      )}
    </section>
  );
}
