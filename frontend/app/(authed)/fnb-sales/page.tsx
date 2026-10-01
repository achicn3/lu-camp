"use client";
// /fnb-sales 餐飲交易紀錄（docs/47，2026-10-01 裁示）：只列含餐點的交易，可對餐點部分退款。
//
// - 同一張單的二手商品只列出、不在這裡退（引導到「交易紀錄」）：二手有序號品、寄售、
//   贈品等專屬規則，兩頁共用同一個退貨引擎，金額一定對得起來。
// - 退款金額、退款去向（餐點只退現金／LINE Pay／台灣Pay）、發票處置全由後端決定，
//   這頁與退款對話框只呈現。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";

import { INVOICE_STATUS_LABELS, PAYMENT_METHOD_LABELS, labelFor } from "@/features/member/labels";
import { ReturnDialog } from "@/features/returns/ReturnDialog";
import { refundTenderLabel } from "@/features/returns/refund";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import {
  exclusiveEndOfTaipeiDay,
  formatTaipeiTime,
  startOfTaipeiDay,
  taipeiDate,
} from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type FnbSale = components["schemas"]["FnbSaleSummaryRead"];

const STATUS_LABELS: Record<string, string> = {
  COMPLETED: "已完成",
  RETURNED: "已全部退款",
  VOIDED: "已作廢",
};

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function money(value: string): string {
  return `$${formatNtd(parseNtd(value) ?? 0)}`;
}

function seatLabel(sale: FnbSale): string {
  if (sale.service_mode === "TAKEOUT") return "外帶";
  return sale.table_no ? `內用 ${sale.table_no}` : "內用";
}

/** 這筆還有沒有餐點可退：作廢／全退的不行，餐點已退金額達餐點小計也不行。 */
function canRefundFood(sale: FnbSale): boolean {
  if (sale.status !== "COMPLETED") return false;
  return (parseNtd(sale.food_refunded) ?? 0) < (parseNtd(sale.food_subtotal) ?? 0);
}

export default function FnbSalesPage() {
  const queryClient = useQueryClient();
  const isManager = useMemo(() => decodeSession()?.role === "MANAGER", []);
  const [day, setDay] = useState(taipeiDate());
  const [target, setTarget] = useState<FnbSale | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["fnb-sales", day],
    queryFn: async (): Promise<FnbSale[]> => {
      const { data, error } = await api.GET("/api/v1/sales/fnb", {
        params: {
          query: { from: startOfTaipeiDay(day), to: exclusiveEndOfTaipeiDay(day), limit: 200 },
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取餐飲交易紀錄失敗");
      return data;
    },
  });
  const rows = list.data ?? [];

  return (
    <section>
      <h1 className="page-title">餐飲交易紀錄</h1>
      <p className="hint">
        只列有點餐的交易。可以退其中幾份餐點（例如 3 杯拿鐵退 1 杯），金額由系統依實付計算；
        同一張單的二手商品請到<Link href="/sales">交易紀錄</Link>退貨。
      </p>

      <div className="card fnb-toolbar">
        <label className="field">
          <span className="field-label">日期</span>
          <input
            type="date"
            value={day}
            max={taipeiDate()}
            onChange={(e) => e.target.value !== "" && setDay(e.target.value)}
          />
        </label>
      </div>

      {notice !== null && (
        <p role="status" className="form-success">
          {notice}
        </p>
      )}
      {list.isError && (
        <p role="alert" className="form-error">
          {list.error.message}{" "}
          <button type="button" onClick={() => void list.refetch()}>
            重試
          </button>
        </p>
      )}
      {list.isPending && <p className="hint">載入中…</p>}
      {list.isSuccess && rows.length === 0 && <p className="hint">這天沒有餐飲交易。</p>}

      {rows.length > 0 && (
        <div className="card">
          <table className="data-table fnb-table">
            <thead>
              <tr>
                <th>時間</th>
                <th>單號</th>
                <th>內用／外帶</th>
                <th>餐點</th>
                <th>餐點小計</th>
                <th>整單總額</th>
                <th>已退</th>
                <th>付款／發票</th>
                <th>狀態</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((sale) => (
                <tr key={sale.id}>
                  <td>{formatTaipeiTime(sale.created_at)}</td>
                  <td>#{sale.id}</td>
                  <td>{seatLabel(sale)}</td>
                  <td>
                    {sale.food_items}
                    {sale.has_other_items && (
                      <span className="fnb-mixed-badge" title="二手商品請到交易紀錄退貨">
                        含二手商品
                      </span>
                    )}
                  </td>
                  <td className="money">{money(sale.food_subtotal)}</td>
                  <td className="money">{money(sale.total)}</td>
                  <td className="money">
                    {(parseNtd(sale.total_refunded) ?? 0) === 0 ? "—" : money(sale.total_refunded)}
                  </td>
                  <td>
                    {labelFor(PAYMENT_METHOD_LABELS, sale.payment_method)}／
                    {labelFor(INVOICE_STATUS_LABELS, sale.invoice_status)}
                  </td>
                  <td>{STATUS_LABELS[sale.status] ?? sale.status}</td>
                  <td>
                    <button
                      type="button"
                      className="btn-ghost"
                      aria-label={`餐點退款 ${sale.id}`}
                      disabled={!canRefundFood(sale)}
                      onClick={() => {
                        setNotice(null);
                        setTarget(sale);
                      }}
                    >
                      退款
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {target !== null && (
        <ReturnDialog
          scope="food"
          sale={{ id: target.id, buyer_contact_id: target.buyer_contact_id ?? null }}
          canConfirmPaper={isManager}
          onClose={() => setTarget(null)}
          onReturned={(refund, tenders) => {
            const split = tenders
              .map(
                (tender) =>
                  `${refundTenderLabel[tender.tender_type]} $${formatNtd(parseNtd(tender.amount) ?? 0)}`,
              )
              .join("、");
            setNotice(`#${target.id} 已退款 $${formatNtd(refund)}（${split}）`);
            setTarget(null);
            // 已退金額、退貨預覽、單筆明細、POS 份數（勾還能賣時）都要重讀。
            for (const key of [
              ["fnb-sales"],
              ["sales"],
              ["sale-detail"],
              ["return-preview"],
              ["menu-items"],
              ["menu-daily-stock"],
            ]) {
              void queryClient.invalidateQueries({ queryKey: key });
            }
          }}
        />
      )}
    </section>
  );
}
