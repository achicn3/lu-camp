"use client";
// /purchasing/invoices/[id] 一張進項發票：管理者可直接修改或刪除（docs/70 §2）；店員只看。
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { InputInvoiceForm } from "@/features/purchasing/InputInvoiceForm";
import {
  dt,
  extractDetail,
  type InputInvoice,
  money,
} from "@/features/purchasing/shared";
import { api } from "@/lib/api";
import { decodeSession } from "@/lib/auth";

function InvoiceView({ invoice }: { invoice: InputInvoice }) {
  return (
    <div className="card pur-detail">
      <div className="pur-detail-head">
        <h2>發票 {invoice.invoice_number}</h2>
      </div>
      <dl className="pur-detail-grid">
        <div>
          <dt>供應商</dt>
          <dd>{invoice.supplier_name}</dd>
        </div>
        <div>
          <dt>發票日期</dt>
          <dd>{invoice.invoice_date}</dd>
        </div>
        <div>
          <dt>含稅金額</dt>
          <dd className="money">{money(invoice.invoice_total)}</dd>
        </div>
        <div>
          <dt>未稅／稅額</dt>
          <dd className="money">
            {money(invoice.invoice_net)}／{money(invoice.invoice_tax)}
          </dd>
        </div>
      </dl>
      <h3>涵蓋的收貨</h3>
      <ul className="pur-receipts-list">
        {invoice.receipts.map((r) => (
          <li key={r.receipt_id}>
            <Link href={`/purchasing/${r.purchase_order_id}`}>採購單 #{r.purchase_order_id}</Link>
            ・{dt(r.received_at)}・{money(r.amount)}
          </li>
        ))}
      </ul>
      <p className="hint">登錄後要修改或刪除，請找管理者。</p>
    </div>
  );
}

export default function InputInvoicePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const invoiceId = Number(id);
  const valid = Number.isInteger(invoiceId) && invoiceId > 0;
  const [saved, setSaved] = useState(false);
  const invoice = useQuery({
    queryKey: ["input-invoices", "detail", invoiceId],
    enabled: valid,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/purchase-input-invoices/{invoice_id}", {
        params: { path: { invoice_id: invoiceId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "找不到進項發票");
      return data;
    },
  });
  const isManager = decodeSession()?.role === "MANAGER";

  return (
    <section className="pur-page">
      <Link href="/purchasing?tab=invoices" className="pur-back">
        ← 回進項發票
      </Link>
      <h1 className="page-title">進項發票</h1>
      {saved && (
        <p role="status" className="hint pur-notice">
          已儲存。
        </p>
      )}
      {!valid ? (
        <p role="alert" className="form-error">
          找不到進項發票
        </p>
      ) : invoice.isPending ? (
        <p>載入中…</p>
      ) : invoice.isError ? (
        <p role="alert" className="form-error">
          {invoice.error.message}
        </p>
      ) : isManager ? (
        <InputInvoiceForm
          // 儲存後以新資料重新帶入表單。
          key={invoice.data.invoice_number + invoice.dataUpdatedAt}
          invoice={invoice.data}
          onSaved={() => {
            setSaved(true);
            void invoice.refetch();
          }}
          onDeleted={() => router.push("/purchasing?tab=invoices")}
        />
      ) : (
        <InvoiceView invoice={invoice.data} />
      )}
    </section>
  );
}
