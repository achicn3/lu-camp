"use client";
// 採購單清單（滿版）：狀態篩選、單號／供應商搜尋、翻頁；點一列進明細頁。
// 收貨、取消等動作都在明細頁做，清單只放「送出草稿」與「收貨入庫」兩個最常用的捷徑。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Pagination } from "@/features/common/Pagination";
import { canReceive, canSubmit, poStatusBadge } from "@/features/purchasing/purchasing";
import {
  dt,
  extractDetail,
  money,
  PAGE_SIZE,
  PO_STATUS_FILTERS,
  type PoStatus,
  type PurchaseOrder,
} from "@/features/purchasing/shared";
import { api } from "@/lib/api";

export function PurchaseOrderTable() {
  const router = useRouter();
  const queryClient = useQueryClient();
  // 預設「待收貨」＝ORDERED＋PARTIAL——最常用；要看全部/草稿/已取消可切籤。
  const [statusKey, setStatusKey] = useState("OUTSTANDING");
  const [page, setPage] = useState(0);
  // 單號/供應商搜尋（提交式）：輸入框與已提交值分開，避免每次按鍵都打 API。
  const [search, setSearch] = useState("");
  const [submittedSearch, setSubmittedSearch] = useState("");
  const [rowError, setRowError] = useState<string | null>(null);
  const statuses = useMemo(
    () => PO_STATUS_FILTERS.find((f) => f.key === statusKey)?.statuses ?? [],
    [statusKey],
  );

  const query = (): { status?: PoStatus[]; q?: string } => ({
    ...(statuses.length > 0 ? { status: statuses } : {}),
    ...(submittedSearch ? { q: submittedSearch } : {}),
  });

  // 總筆數（key 帶 page：換頁時一併重抓，別台新增的資料才會反映在「共 N 頁」）。
  const ordersTotal = useQuery({
    queryKey: ["purchase-orders", "count", statusKey, submittedSearch, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/purchase-orders/count", {
        params: { query: query() },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取採購單總筆數失敗");
      return data.count;
    },
  });

  const orders = useQuery({
    queryKey: ["purchase-orders", statusKey, submittedSearch, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/purchase-orders", {
        params: { query: { ...query(), limit: PAGE_SIZE, offset: page * PAGE_SIZE } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取採購單失敗");
      return data;
    },
  });

  const submit = useMutation({
    mutationFn: async (po: PurchaseOrder) => {
      const { data, error } = await api.POST("/api/v1/purchase-orders/{purchase_order_id}/submit", {
        params: { path: { purchase_order_id: po.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "送出失敗");
      return data;
    },
    onSuccess: () => {
      setRowError(null);
      void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
    },
    onError: (err: Error) => setRowError(err.message),
  });

  const rows = orders.data ?? [];

  return (
    <div className="card pur-orders">
      <div className="pur-orders-toolbar">
        <div className="settle-tabs" aria-label="採購單狀態篩選">
          {PO_STATUS_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`chip ${statusKey === f.key ? "chip-active" : ""}`}
              onClick={() => {
                setStatusKey(f.key);
                setPage(0);
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
        <form
          className="member-allsearch"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(0);
            setSubmittedSearch(search.trim());
          }}
        >
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="以單號或供應商搜尋"
            aria-label="採購單搜尋"
          />
          <button type="submit" className="btn-secondary">
            搜尋
          </button>
          {submittedSearch && (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setSearch("");
                setSubmittedSearch("");
                setPage(0);
              }}
            >
              清除（{submittedSearch}）
            </button>
          )}
        </form>
      </div>
      {rowError !== null && (
        <p role="alert" className="form-error">
          {rowError}
        </p>
      )}
      {orders.isPending ? (
        <p>載入中…</p>
      ) : orders.isError ? (
        <p role="alert" className="form-error">
          {orders.error.message}
        </p>
      ) : rows.length === 0 ? (
        <div className="empty-state pur-orders-empty">
          <p>{page === 0 ? "尚無符合的採購單。" : "沒有更多採購單了。"}</p>
          {page === 0 && (
            <Link href="/purchasing/new" className="btn-primary">
              ＋ 建立採購單
            </Link>
          )}
        </div>
      ) : (
        <div className="pur-order-wrap">
          <table className="data-table pur-order-table">
            <thead>
              <tr>
                <th>單號</th>
                <th>供應商</th>
                <th>建立 / 下單時間</th>
                <th>品項</th>
                <th>到貨</th>
                <th>總額</th>
                <th>狀態</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((po) => {
                const badge = poStatusBadge(po.status);
                const ordered = po.lines.reduce((sum, l) => sum + l.qty, 0);
                const received = po.lines.reduce((sum, l) => sum + l.received_qty, 0);
                return (
                  <tr
                    key={po.id}
                    className="pur-order-row"
                    onClick={() => router.push(`/purchasing/${po.id}`)}
                  >
                    <td>
                      <Link
                        href={`/purchasing/${po.id}`}
                        className="pur-po-link"
                        onClick={(e) => e.stopPropagation()}
                      >
                        #{po.id}
                      </Link>
                    </td>
                    <td>{po.supplier_name}</td>
                    <td>
                      {dt(po.status === "DRAFT" ? po.created_at : po.ordered_at)}
                      {po.received_at && (
                        <span className="row-sub">收貨 {dt(po.received_at)}</span>
                      )}
                    </td>
                    <td>{po.lines.length} 項</td>
                    <td>
                      {received} / {ordered}
                    </td>
                    <td className="money">{money(po.total_cost)}</td>
                    <td>
                      <span className={`inv-badge inv-tone-${badge.tone}`}>{badge.label}</span>
                    </td>
                    {/* td 本身不能 display:flex（會脫離表格排版、框線錯位），動作包一層 div。 */}
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="pur-row-actions">
                        {canSubmit(po.status) && (
                          <button
                            type="button"
                            className="btn-primary"
                            disabled={submit.isPending}
                            onClick={() => submit.mutate(po)}
                          >
                            送出
                          </button>
                        )}
                        {canReceive(po.status) && (
                          <Link
                            href={`/purchasing/${po.id}?receive=1`}
                            className="btn-primary"
                          >
                            收貨入庫
                          </Link>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!orders.isPending && !orders.isError && (
        <Pagination
          page={page}
          count={rows.length}
          pageSize={PAGE_SIZE}
          total={ordersTotal.isError ? undefined : ordersTotal.data}
          unit="張"
          onPage={setPage}
        />
      )}
    </div>
  );
}
