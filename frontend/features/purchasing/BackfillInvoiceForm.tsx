"use client";
// 進項發票補登（某收貨批次漏登時；登錄後不可覆寫）。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { extractDetail } from "@/features/purchasing/shared";
import { api } from "@/lib/api";

export function BackfillInvoiceForm({ poId, receiptId }: { poId: number; receiptId: number }) {
  const queryClient = useQueryClient();
  const [number, setNumber] = useState("");
  const [dateStr, setDateStr] = useState("");
  const [net, setNet] = useState("");
  const [tax, setTax] = useState("");
  const [total, setTotal] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const backfill = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST(
        "/api/v1/purchase-orders/{purchase_order_id}/receipts/{receipt_id}/invoice",
        {
          params: { path: { purchase_order_id: poId, receipt_id: receiptId } },
          body: {
            invoice_number: number.trim().toUpperCase(),
            invoice_date: dateStr,
            invoice_net: net.trim(),
            invoice_tax: tax.trim(),
            invoice_total: total.trim(),
          },
        },
      );
      if (!data) throw new Error(extractDetail(error) ?? "補登失敗");
      return data;
    },
    onSuccess: () => {
      setNote("已補登進項發票");
      void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    },
    onError: (e: Error) => setNote(e.message),
  });
  return (
    <div className="pur-backfill-invoice">
      <h3>補登進項發票</h3>
      <div className="pur-invoice-row">
        <input
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          placeholder="AB12345678"
          maxLength={10}
          aria-label="補登發票號碼"
        />
        <input
          type="date"
          value={dateStr}
          onChange={(e) => setDateStr(e.target.value)}
          aria-label="補登發票日期"
        />
        <input
          value={net}
          onChange={(e) => setNet(e.target.value)}
          inputMode="numeric"
          placeholder="未稅金額"
          aria-label="補登發票未稅金額"
        />
        <input
          value={tax}
          onChange={(e) => setTax(e.target.value)}
          inputMode="numeric"
          placeholder="稅額"
          aria-label="補登發票稅額"
        />
        <input
          value={total}
          onChange={(e) => setTotal(e.target.value)}
          inputMode="numeric"
          placeholder="含稅金額"
          aria-label="補登發票含稅金額"
        />
        <button
          type="button"
          className="btn-secondary"
          disabled={backfill.isPending || !number || !dateStr || !net || !tax || !total}
          onClick={() => backfill.mutate()}
        >
          補登
        </button>
      </div>
      {note !== null && <p className="hint">{note}</p>}
    </div>
  );
}
