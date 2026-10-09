"use client";
// /purchasing/invoices/[id] 一張進項發票：管理者可直接修改或刪除（docs/70 §2）；店員只看。
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);
  // 每存一次 +1：表單照存好的內容重新帶入；背景重抓不會把正在打的內容洗掉。
  const [version, setVersion] = useState(0);
  const detailKey = ["input-invoices", "detail", invoiceId];
  const invoice = useQuery({
    queryKey: detailKey,
    enabled: valid,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/purchase-input-invoices/{invoice_id}", {
        params: { path: { invoice_id: invoiceId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "找不到進項發票");
      return data;
    },
    // 一定要拿伺服器最新的一份才給改：舊快取會漏掉剛掛上的收貨，存了就把它拿掉（Codex 第三輪）。
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
  });
  const loading = invoice.isPending || (!invoice.isFetchedAfterMount && invoice.isFetching);
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
      ) : loading ? (
        <p>載入中…</p>
      ) : invoice.isError ? (
        <p role="alert" className="form-error">
          {invoice.error.message}
        </p>
      ) : isManager ? (
        <InputInvoiceForm
          key={version}
          invoice={invoice.data}
          onSaved={(updated) => {
            queryClient.setQueryData(detailKey, updated);
            setVersion((v) => v + 1);
            setSaved(true);
          }}
          onDeleted={() => router.push("/purchasing?tab=invoices")}
        />
      ) : (
        <InvoiceView invoice={invoice.data} />
      )}
    </section>
  );
}
