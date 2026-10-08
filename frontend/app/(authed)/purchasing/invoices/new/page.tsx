"use client";
// /purchasing/invoices/new 登錄進項發票（docs/70 §5.3）。
// ?supplier=5&receipt=12＝從採購單明細某批收貨的「登錄發票」進來，先帶入供應商並勾好那一批。
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { InputInvoiceForm } from "@/features/purchasing/InputInvoiceForm";

function positiveInt(raw: string | null): number | null {
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function NewInvoice() {
  const router = useRouter();
  const params = useSearchParams();
  const receipt = positiveInt(params.get("receipt"));
  return (
    <InputInvoiceForm
      initialSupplierId={positiveInt(params.get("supplier"))}
      initialReceiptIds={receipt === null ? [] : [receipt]}
      onSaved={(invoice) => router.push(`/purchasing/invoices/${invoice.id}`)}
    />
  );
}

export default function NewInputInvoicePage() {
  return (
    <section className="pur-page">
      <Link href="/purchasing?tab=invoices" className="pur-back">
        ← 回進項發票
      </Link>
      <h1 className="page-title">登錄進項發票</h1>
      <Suspense fallback={<p>載入中…</p>}>
        <NewInvoice />
      </Suspense>
    </section>
  );
}
