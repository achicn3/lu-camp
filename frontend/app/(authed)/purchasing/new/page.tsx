"use client";
// /purchasing/new 建立採購單（獨立頁）。?reorder=1,2,3＝從低庫存提醒帶入的商品。
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { CreatePurchaseOrderForm } from "@/features/purchasing/CreatePurchaseOrderForm";

function parseIds(raw: string | null): number[] {
  return (raw ?? "")
    .split(",")
    .map((part) => Number(part))
    .filter((id) => Number.isInteger(id) && id > 0);
}

function NewPurchaseOrder() {
  const router = useRouter();
  const params = useSearchParams();
  return (
    <CreatePurchaseOrderForm
      initialProductIds={parseIds(params.get("reorder"))}
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
