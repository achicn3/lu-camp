"use client";
// /purchasing/new 建立採購單（獨立頁）。?reorder=42:3,43:5＝從低庫存提醒帶入的商品與建議數量。
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import {
  CreatePurchaseOrderForm,
  type ReorderItem,
} from "@/features/purchasing/CreatePurchaseOrderForm";

/** reorder=42:3,43 → [{id:42, qty:3}, {id:43}]；壞掉的片段略過。 */
function parseReorder(raw: string | null): ReorderItem[] {
  return (raw ?? "").split(",").flatMap((part) => {
    const [idText, qtyText] = part.split(":");
    const id = Number(idText);
    if (!Number.isInteger(id) || id <= 0) return [];
    const qty = Number(qtyText);
    return [Number.isInteger(qty) && qty > 0 ? { id, qty } : { id }];
  });
}

function NewPurchaseOrder() {
  const router = useRouter();
  const params = useSearchParams();
  return (
    <CreatePurchaseOrderForm
      initialItems={parseReorder(params.get("reorder"))}
      onCreated={(po) => router.push(`/purchasing/${po.id}`)}
    />
  );
}

export default function NewPurchaseOrderPage() {
  return (
    <section className="pur-page">
      <Link href="/purchasing" className="pur-back">
        ← 回採購單列表
      </Link>
      <h1 className="page-title">建立採購單</h1>
      {/* useSearchParams 需要 Suspense 邊界（Next App Router）。 */}
      <Suspense fallback={<p>載入中…</p>}>
        <NewPurchaseOrder />
      </Suspense>
    </section>
  );
}
