"use client";
// 整批收購明細（含簽名）列印：付款後的畫面與待整理上架頁共用（docs/42 §6）。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import { printAcquisitionReceipt } from "@/lib/agent";
import { fetchSignaturePngBase64 } from "@/lib/signature";

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

/** 內容由後端依客人簽的那份組好；回傳 mutation 與給店員看的結果訊息。 */
export function useIntakeReceiptPrint(batchId: number) {
  const [note, setNote] = useState<string | null>(null);
  const print = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.GET("/api/v1/intake-batches/{batch_id}/receipt", {
        params: { path: { batch_id: batchId } },
      });
      if (!data) throw new Error(detail(error) ?? "讀不到收購明細");
      await printAcquisitionReceipt({
        storeId: data.store_id,
        acquisitionId: data.acquisition_id,
        reference: data.reference,
        sellerName: data.seller_name,
        items: data.items,
        total: data.total,
        payoutMethod: data.payout_method,
        createdAt: data.signed_at,
        signaturePngBase64: await fetchSignaturePngBase64(data.signature_task_id),
        storeCreditGranted: data.store_credit_granted ?? undefined,
        storeCreditBalanceAfter: data.store_credit_balance_after ?? undefined,
      });
    },
    onSuccess: () => setNote("收購明細已送出列印，請交給客人。"),
    onError: (e: Error) => setNote(`收購明細沒有印出來：${e.message}`),
  });
  return { print, note };
}
