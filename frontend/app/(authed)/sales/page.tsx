"use client";
// /sales 交易紀錄（當日）：打錯單的現場救援入口——列出今日銷售、店長可作廢（二次確認，
// docs/10 §28 危險動作）。作廢由後端整套反轉：庫存回補、點數/購物金沖回、寄售結算反轉、
// 電子發票中止；已退貨/已作廢的單後端會擋（409），前端先行停用按鈕。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { INVOICE_STATUS_LABELS, labelFor } from "@/features/member/labels";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import { formatNtd, parseNtd } from "@/lib/money";

type SaleSummary = components["schemas"]["SaleSummaryRead"];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function useIsManager(): boolean {
  return useMemo(() => decodeSession()?.role === "MANAGER", []);
}

/** 今日 00:00（本地時區）→ ISO；「當日交易」以門市營業日直覺為準。 */
function startOfTodayIso(): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString("zh-TW", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

const SALE_STATUS_LABELS: Record<string, string> = {
  COMPLETED: "已完成",
  RETURNED: "已退貨",
};

function VoidConfirmDialog({
  sale,
  onClose,
  onVoided,
}: {
  sale: SaleSummary;
  onClose: () => void;
  onVoided: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const voidSale = useMutation({
    mutationFn: async () => {
      const { data, error: apiError } = await api.POST("/api/v1/sales/{sale_id}/void", {
        params: { path: { sale_id: sale.id } },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "作廢失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onVoided();
    },
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="作廢銷售確認"
    >
      <div className="card pos-dialog">
        <h2>作廢銷售 #{sale.id}？</h2>
        <p>
          總額 <span className="money">${formatNtd(parseNtd(sale.total) ?? 0)}</span>
          ，作廢後庫存回補、點數與購物金沖回、寄售結算反轉，且無法復原。
        </p>
        <p className="hint">現金退還請直接自錢櫃取出，關帳對帳會核對差異。</p>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button
            type="button"
            className="btn-danger"
            onClick={() => voidSale.mutate()}
            disabled={voidSale.isPending}
          >
            {voidSale.isPending ? "作廢中…" : "確認作廢"}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SalesPage() {
  const isManager = useIsManager();
  const queryClient = useQueryClient();
  const [voidTarget, setVoidTarget] = useState<SaleSummary | null>(null);
  const [voidedNote, setVoidedNote] = useState<string | null>(null);
  // 交易紀錄簽收（docs/23 K5b）：推 TRANSACTION_ACK 至手持裝置，客人核對後簽名留存（不擋流程）。
  const [ackNote, setAckNote] = useState<string | null>(null);
  const pushAck = useMutation({
    mutationFn: async (sale: SaleSummary) => {
      if (sale.buyer_contact_id == null) throw new Error("此單無買方會員，無法推送簽收");
      // content 由後端以銷售單為準重建（單號/總額/時間），客端不提供（Codex K5 第三輪：
      // 簽收證據不可由客端敘述）。
      const { data, error } = await api.POST("/api/v1/signing/tasks", {
        body: {
          kind: "TRANSACTION_ACK",
          contact_id: sale.buyer_contact_id,
          content: {},
          ref_type: "sale",
          ref_id: sale.id,
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "推送簽收失敗");
      return sale.id;
    },
    onSuccess: (saleId) => setAckNote(`已推送 #${saleId} 交易紀錄簽收至手持裝置`),
    onError: (e: Error) => setAckNote(e.message),
  });

  const sales = useQuery({
    queryKey: ["sales", "today"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/sales", {
        params: { query: { from: startOfTodayIso(), limit: 200 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取交易紀錄失敗");
      return data;
    },
  });

  const rows = sales.data ?? [];

  return (
    <section>
      <h1 className="page-title">交易紀錄（今日）</h1>
      <p className="hint">
        打錯單請在此作廢（限店長）。已退貨的單不可作廢，請走退貨流程處理剩餘部分。
      </p>
      {voidedNote !== null && <p className="form-success">{voidedNote}</p>}
      {ackNote !== null && <p className="hint">{ackNote}</p>}
      {sales.isError && (
        <p role="alert" className="form-error">
          {(sales.error as Error).message}
        </p>
      )}
      {sales.isSuccess && rows.length === 0 && <p className="hint">今日尚無交易。</p>}
      {rows.length > 0 && (
        <div className="card">
          <table className="data-table sales-list">
          <thead>
            <tr>
              <th>時間</th>
              <th>單號</th>
              <th>總額</th>
              <th>發票狀態</th>
              <th>狀態</th>
              <th aria-label="簽收" />
              {isManager && <th aria-label="操作" />}
            </tr>
          </thead>
          <tbody>
            {rows.map((sale) => {
              const voided = sale.invoice_status === "VOID";
              const returned = sale.status === "RETURNED";
              return (
                <tr key={sale.id}>
                  <td>{timeLabel(sale.created_at)}</td>
                  <td>#{sale.id}</td>
                  <td>
                    <span className="money">${formatNtd(parseNtd(sale.total) ?? 0)}</span>
                  </td>
                  <td>{labelFor(INVOICE_STATUS_LABELS, sale.invoice_status)}</td>
                  <td>{voided ? "已作廢" : labelFor(SALE_STATUS_LABELS, sale.status)}</td>
                  <td>
                    {!voided && !returned && sale.buyer_contact_id != null && (
                      <button
                        type="button"
                        className="btn-ghost"
                        aria-label={`推送銷售 ${sale.id} 簽收`}
                        disabled={pushAck.isPending}
                        onClick={() => {
                          setAckNote(null);
                          pushAck.mutate(sale);
                        }}
                      >
                        推送簽收
                      </button>
                    )}
                  </td>
                  {isManager && (
                    <td>
                      {!voided && !returned && (
                        <button
                          type="button"
                          className="btn-danger"
                          aria-label={`作廢銷售 ${sale.id}`}
                          onClick={() => {
                            setVoidedNote(null);
                            setVoidTarget(sale);
                          }}
                        >
                          作廢
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
          </table>
        </div>
      )}
      {voidTarget !== null && (
        <VoidConfirmDialog
          sale={voidTarget}
          onClose={() => setVoidTarget(null)}
          onVoided={() => {
            setVoidedNote(`銷售 #${voidTarget.id} 已作廢。`);
            setVoidTarget(null);
            void queryClient.invalidateQueries({ queryKey: ["sales", "today"] });
          }}
        />
      )}
    </section>
  );
}
