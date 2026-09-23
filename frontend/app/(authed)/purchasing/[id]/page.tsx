"use client";
// /purchasing/[id] 採購單明細（取代原本的彈窗）。?receive=1＝從清單按「收貨入庫」進來，直接開收貨。
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { PurchaseOrderDetail } from "@/features/purchasing/PurchaseOrderDetail";

function Detail() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const poId = Number(id);
  if (!Number.isInteger(poId) || poId <= 0) {
    return (
      <p role="alert" className="form-error">
        找不到採購單
      </p>
    );
  }
  return <PurchaseOrderDetail poId={poId} openReceive={params.get("receive") === "1"} />;
}

export default function PurchaseOrderPage() {
  return (
    <section className="pur-page">
      <Suspense fallback={<p>載入中…</p>}>
        <Detail />
      </Suspense>
    </section>
  );
}
