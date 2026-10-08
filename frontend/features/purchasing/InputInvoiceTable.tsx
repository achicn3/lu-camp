"use client";
// 進項發票清單（docs/70 §5.3）：發票日期新到舊、可依供應商篩選、翻頁；點一列看／改那張發票。
// 一張發票可涵蓋好幾批收貨（整月合併開），「涵蓋」欄列出它對到的採購單。
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Pagination } from "@/features/common/Pagination";
import {
  extractDetail,
  invoiceOrders,
  money,
  PAGE_SIZE,
} from "@/features/purchasing/shared";
import { api } from "@/lib/api";

export function InputInvoiceTable() {
  const router = useRouter();
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const filter = supplierId === null ? {} : { supplier_id: supplierId };

  const suppliers = useQuery({
    queryKey: ["suppliers", "all-for-invoices"],
    queryFn: async () =>
      (
        await api.GET("/api/v1/suppliers", {
          params: { query: { include_inactive: true, limit: 200, offset: 0 } },
        })
      ).data ?? [],
  });
  const total = useQuery({
    queryKey: ["input-invoices", "count", supplierId, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/purchase-input-invoices/count", {
        params: { query: filter },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取發票總筆數失敗");
      return data.count;
    },
  });
  const invoices = useQuery({
    queryKey: ["input-invoices", supplierId, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/purchase-input-invoices", {
        params: { query: { ...filter, limit: PAGE_SIZE, offset: page * PAGE_SIZE } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取進項發票失敗");
      return data;
    },
  });
  const rows = invoices.data ?? [];

  return (
    <div className="card pur-invoices">
      <div className="pur-orders-toolbar">
        <label className="field">
          <span className="field-label">供應商</span>
          <select
            aria-label="依供應商篩選"
            value={supplierId ?? ""}
            onChange={(e) => {
              setSupplierId(e.target.value === "" ? null : Number(e.target.value));
              setPage(0);
            }}
          >
            <option value="">全部供應商</option>
            {(suppliers.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <Link href="/purchasing/invoices/new" className="btn-primary">
          ＋ 登錄發票
        </Link>
      </div>

      {invoices.isPending ? (
        <p>載入中…</p>
      ) : invoices.isError ? (
        <p role="alert" className="form-error">
          {invoices.error.message}
        </p>
      ) : rows.length === 0 ? (
        <p className="empty-state">
          還沒有登錄的進項發票。廠商開發票後按「＋ 登錄發票」，勾選它涵蓋的收貨即可。
        </p>
      ) : (
        <div className="pur-order-wrap">
          <table className="data-table pur-order-table">
            <thead>
              <tr>
                <th>發票號碼</th>
                <th>發票日期</th>
                <th>供應商</th>
                <th>含稅金額</th>
                <th>涵蓋的採購單</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((invoice) => (
                <tr
                  key={invoice.id}
                  className="pur-order-row"
                  onClick={() => router.push(`/purchasing/invoices/${invoice.id}`)}
                >
                  <td>
                    <Link href={`/purchasing/invoices/${invoice.id}`} className="pur-po-link">
                      {invoice.invoice_number}
                    </Link>
                  </td>
                  <td>{invoice.invoice_date}</td>
                  <td>{invoice.supplier_name}</td>
                  <td className="money">{money(invoice.invoice_total)}</td>
                  <td>
                    {invoiceOrders(invoice)}
                    <span className="row-sub">{invoice.receipts.length} 批收貨</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!invoices.isPending && !invoices.isError && (
        <Pagination
          page={page}
          count={rows.length}
          pageSize={PAGE_SIZE}
          total={total.isError ? undefined : total.data}
          unit="張"
          onPage={setPage}
        />
      )}
    </div>
  );
}
