"use client";
// /purchasing/[id]/edit 修改採購單（docs/70 §4）：與建立同一個表單。草稿大家能改；
// 已下單／已收貨只有管理者（後端也擋）；收過貨的單可改「已收」，庫存由後端自動加減。
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { CreatePurchaseOrderForm } from "@/features/purchasing/CreatePurchaseOrderForm";
import { canEdit } from "@/features/purchasing/purchasing";
import { extractDetail } from "@/features/purchasing/shared";
import { api } from "@/lib/api";
import { decodeSession } from "@/lib/auth";

function EditPurchaseOrder({ poId }: { poId: number }) {
  const router = useRouter();
  const order = useQuery({
    queryKey: ["purchase-orders", "detail", poId],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/purchase-orders/{purchase_order_id}", {
        params: { path: { purchase_order_id: poId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取採購單失敗");
      return data;
    },
    // 一定要拿伺服器上最新的一份才帶入表單：沿用快取（例如剛存過再打開）會帶出改之前的數量，
    // 再存一次就把庫存改回去（Codex 第一輪）。表單只在第一次拿到資料時帶入，之後不會被重抓蓋掉。
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
  });
  if (order.isPending || (!order.isFetchedAfterMount && order.isFetching)) {
    return <p>載入中…</p>;
  }
  if (order.isError) {
    return (
      <p role="alert" className="form-error">
        {order.error.message}
      </p>
    );
  }
  const po = order.data;
  if (!canEdit(po.status, decodeSession()?.role === "MANAGER")) {
    return (
      <p role="alert" className="form-error">
        {po.status === "CANCELLED"
          ? "已取消的採購單不能修改。"
          : "已下單或已收貨的採購單只有管理者能修改。"}
      </p>
    );
  }
  return (
    <CreatePurchaseOrderForm
      editing={po}
      onSaved={(saved) => router.push(`/purchasing/${saved.id}`)}
    />
  );
}

export default function EditPurchaseOrderPage() {
  const { id } = useParams<{ id: string }>();
  const poId = Number(id);
  const valid = Number.isInteger(poId) && poId > 0;
  return (
    <section className="pur-page">
      <Link href={valid ? `/purchasing/${poId}` : "/purchasing"} className="pur-back">
        ← 回採購單明細
      </Link>
      <h1 className="page-title">修改採購單{valid ? ` #${poId}` : ""}</h1>
      {valid ? (
        <EditPurchaseOrder poId={poId} />
      ) : (
        <p role="alert" className="form-error">
          找不到採購單
        </p>
      )}
    </section>
  );
}
