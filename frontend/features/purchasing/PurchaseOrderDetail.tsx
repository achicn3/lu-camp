"use client";
// 採購單明細頁：供應商、狀態、時間、逐項訂購／已收／待收、收貨批次與進項發票；
// 草稿可送出、已下單可收貨或取消。收過貨的品項可在這裡直接印標籤（條碼由系統自動產生）。
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { BackfillInvoiceForm } from "@/features/purchasing/BackfillInvoiceForm";
import {
  type CatalogProduct,
  canCancel,
  canReceive,
  canSubmit,
  lineRemaining,
  poStatusBadge,
} from "@/features/purchasing/purchasing";
import { ReceiveDialog } from "@/features/purchasing/ReceiveDialog";
import { dt, extractDetail, money, type PurchaseOrder } from "@/features/purchasing/shared";
import { useCatalogBrandNames } from "@/features/purchasing/useCatalogBrandNames";
import { printLabel } from "@/lib/agent";
import { api } from "@/lib/api";
import { parseNtd } from "@/lib/money";

export function PurchaseOrderDetail({
  poId,
  openReceive = false,
}: {
  poId: number;
  /** 從清單按「收貨入庫」進來：頁面載入後直接打開收貨對話框。 */
  openReceive?: boolean;
}) {
  const queryClient = useQueryClient();
  const [receiving, setReceiving] = useState(openReceive);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const order = useQuery({
    queryKey: ["purchase-orders", "detail", poId],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/purchase-orders/{purchase_order_id}", {
        params: { path: { purchase_order_id: poId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取採購單失敗");
      return data;
    },
  });
  const po = order.data;

  // 明細只記 catalog_product_id；逐一取回品名、品牌與條碼（採購單的品項數不多）。
  const productIds = [...new Set((po?.lines ?? []).map((l) => l.catalog_product_id))];
  const products = useQueries({
    queries: productIds.map((id) => ({
      queryKey: ["catalog-products", "by-id", id],
      queryFn: async (): Promise<CatalogProduct | null> =>
        (
          await api.GET("/api/v1/catalog-products/{product_id}", {
            params: { path: { product_id: id } },
          })
        ).data ?? null,
    })),
  });
  const productById = new Map(
    products.flatMap((q) => (q.data ? [[q.data.id, q.data] as const] : [])),
  );
  const brandName = useCatalogBrandNames();
  const productName = (id: number) => productById.get(id)?.name ?? `#${id}`;

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
  };

  const submit = useMutation({
    mutationFn: async (target: PurchaseOrder) => {
      const { data, error } = await api.POST("/api/v1/purchase-orders/{purchase_order_id}/submit", {
        params: { path: { purchase_order_id: target.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "送出失敗");
      return data;
    },
    onSuccess: () => {
      setActionError(null);
      refresh();
    },
    onError: (err: Error) => setActionError(err.message),
  });

  const cancel = useMutation({
    mutationFn: async (target: PurchaseOrder) => {
      const { data, error } = await api.POST("/api/v1/purchase-orders/{purchase_order_id}/cancel", {
        params: { path: { purchase_order_id: target.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "取消失敗");
      return data;
    },
    onSuccess: () => {
      setActionError(null);
      refresh();
    },
    onError: (err: Error) => setActionError(err.message),
  });

  if (order.isPending) return <p>載入中…</p>;
  if (order.isError || !po) {
    return (
      <p role="alert" className="form-error">
        {order.error?.message ?? "找不到採購單"}
      </p>
    );
  }

  const badge = poStatusBadge(po.status);
  const busy = submit.isPending || cancel.isPending;

  return (
    <div className="pur-detail-page">
      <Link href="/purchasing" className="pur-back">
        ← 回採購單列表
      </Link>
      <div className="card pur-detail">
        <div className="pur-detail-head">
          <h2>採購單 #{po.id}</h2>
          <span className={`inv-badge inv-tone-${badge.tone}`}>{badge.label}</span>
        </div>
        <dl className="pur-detail-grid">
          <div>
            <dt>供應商</dt>
            <dd>{po.supplier_name}</dd>
          </div>
          <div>
            <dt>{po.status === "DRAFT" ? "建立時間" : "下單時間"}</dt>
            <dd>{dt(po.status === "DRAFT" ? po.created_at : po.ordered_at)}</dd>
          </div>
          <div>
            <dt>收貨完成</dt>
            <dd>{dt(po.received_at)}</dd>
          </div>
          <div>
            <dt>合計</dt>
            <dd className="money">{money(po.total_cost)}</dd>
          </div>
        </dl>

        {notice !== null && (
          <p role="status" className="hint pur-notice">
            {notice}
          </p>
        )}
        {actionError !== null && (
          <p role="alert" className="form-error">
            {actionError}
          </p>
        )}

        <div className="pur-lines-wrap">
          <table className="data-table pur-detail-table">
            <thead>
              <tr>
                <th>商品</th>
                <th>訂購</th>
                <th>已收</th>
                <th>待收</th>
                <th>進貨單價</th>
                <th>售價</th>
                <th>小計</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {po.lines.map((line) => {
                const product = productById.get(line.catalog_product_id) ?? null;
                const remaining = lineRemaining(line.qty, line.received_qty);
                return (
                  <tr key={line.id}>
                    <td>
                      {product ? product.name : `#${line.catalog_product_id}`}
                      {product && product.brand_id !== null && (
                        <span className="row-sub">{brandName(product.brand_id) ?? ""}</span>
                      )}
                    </td>
                    <td>{line.qty}</td>
                    <td>{line.received_qty}</td>
                    <td className={remaining > 0 ? "pur-remaining" : ""}>{remaining}</td>
                    <td className="money">{money(line.unit_cost)}</td>
                    <td className="money">{product ? money(product.unit_price) : "—"}</td>
                    <td className="money">{money(line.line_total)}</td>
                    <td>
                      {product && line.received_qty > 0 && (
                        <LabelButton product={product} brand={brandName(product.brand_id)} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {po.receipts.length > 0 && (
          <div className="pur-receipts">
            <h3>收貨批次</h3>
            <ul className="pur-receipts-list">
              {po.receipts.map((r, idx) => (
                <li key={r.id}>
                  <span className="pur-receipt-head">
                    第 {idx + 1} 批・{dt(r.received_at)}
                  </span>
                  {r.invoice ? (
                    <span className="row-sub">
                      發票 {r.invoice.invoice_number}（{r.invoice.invoice_date}）含稅{" "}
                      {money(r.invoice.invoice_total)}｜未稅 {money(r.invoice.invoice_net)}／稅{" "}
                      {money(r.invoice.invoice_tax)}
                    </span>
                  ) : (
                    <BackfillInvoiceForm poId={po.id} receiptId={r.id} />
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {(canSubmit(po.status) || canReceive(po.status) || canCancel(po.status)) && (
          <div className="pur-detail-actions">
            {canSubmit(po.status) && (
              <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={() => submit.mutate(po)}
              >
                {submit.isPending ? "送出中…" : "送出採購"}
              </button>
            )}
            {canReceive(po.status) && (
              <button type="button" className="btn-primary" onClick={() => setReceiving(true)}>
                收貨入庫
              </button>
            )}
            {canCancel(po.status) && (
              <button
                type="button"
                className="btn-ghost pur-cancel-btn"
                disabled={busy}
                onClick={() => cancel.mutate(po)}
              >
                {cancel.isPending ? "取消中…" : "取消採購單"}
              </button>
            )}
          </div>
        )}
      </div>

      {receiving && canReceive(po.status) && (
        <ReceiveDialog
          po={po}
          productName={productName}
          onClose={() => setReceiving(false)}
          onReceived={(message) => {
            setReceiving(false);
            setNotice(message ?? "已收貨入庫。可在明細右側為收到的商品印標籤。");
          }}
        />
      )}
    </div>
  );
}

// 採購來的一般商品一律印「全新」；條碼是系統自動產生的商品編號（店員不必輸入）。
function LabelButton({
  product,
  brand,
}: {
  product: CatalogProduct;
  brand: string | null | undefined;
}) {
  const print = useMutation({
    mutationFn: () => {
      if (brand === undefined) throw new Error("品牌名稱尚未取得，請重新整理後再試");
      return printLabel(product.sku, product.name, parseNtd(product.unit_price) ?? 0, {
        brand,
        condition: "全新",
      });
    },
  });
  return (
    <span className="inv-reprint">
      <button
        type="button"
        className="btn-ghost"
        onClick={() => print.mutate()}
        disabled={print.isPending || brand === undefined}
        aria-label={`印標籤 ${product.name}`}
      >
        {print.isPending ? "列印中…" : "印標籤"}
      </button>
      {print.isSuccess && <span className="inv-reprint-ok">✓ 已送出</span>}
      {print.isError && (
        <span className="form-error" title={print.error.message}>
          ✗ 失敗
        </span>
      )}
    </span>
  );
}
