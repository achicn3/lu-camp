"use client";
// /consignment 寄售付款工作台（Phase 4 / 4A-2）：待付款清單 → 二次確認 → 現金出帳。
// 後端負責交易原子性、開帳守衛、idempotency 與稽核；前端只做清楚的工作流與防呆。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Pagination } from "@/features/common/Pagination";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";
import { newIdempotencyKey } from "@/lib/uuid";

type Settlement = components["schemas"]["ConsignmentSettlementRead"];
type SettlementStatus = components["schemas"]["ConsignmentSettlementStatus"];

const PAGE_SIZE = 50;

const STATUS_TABS: { status: SettlementStatus; label: string }[] = [
  { status: "PENDING", label: "待付款" },
  { status: "PAID", label: "已付款" },
  { status: "CANCELLED", label: "已取消" },
];

const STATUS_LABELS: Record<SettlementStatus, string> = {
  PENDING: "待付款",
  PAID: "已付款",
  CANCELLED: "已取消",
};

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function money(value: string): string {
  const parsed = parseNtd(value);
  return parsed === null ? value : formatNtd(parsed);
}

function dt(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-TW");
}

function SettlementStatusBadge({ row }: { row: Settlement }) {
  return (
    <span className={`settle-badge settle-${row.status.toLowerCase()}`}>
      {STATUS_LABELS[row.status]}
    </span>
  );
}

function ReclaimFlag({ row }: { row: Settlement }) {
  if (!row.reclaim_needed) return null;
  return <span className="settle-badge settle-reclaim">需追回</span>;
}

function ConfirmDialog({
  row,
  onCancel,
  onConfirm,
  pending,
  error,
}: {
  row: Settlement;
  onCancel: () => void;
  onConfirm: () => void;
  pending: boolean;
  error: string | null;
}) {
  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="確認付款">
      <div className="card pos-dialog">
        <h2>確認支付寄售款</h2>
        <p className="hint">
          將支付 {row.consignor_name ?? "寄售人"} ${money(row.payout_amount)}，系統成功後再交付現金。
        </p>
        <dl className="settle-confirm">
          <div>
            <dt>商品</dt>
            <dd>
              {row.item_name ?? "—"}
              <span className="row-sub">{row.item_code ?? `#${row.serialized_item_id}`}</span>
            </dd>
          </div>
          <div>
            <dt>銷售單</dt>
            <dd>#{row.sale_id}</dd>
          </div>
          <div>
            <dt>應付</dt>
            <dd className="money">{money(row.payout_amount)}</dd>
          </div>
        </dl>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="button" className="btn-primary" onClick={onConfirm} disabled={pending}>
            {pending ? "付款中…" : "確認付款"}
          </button>
          <button type="button" className="btn-ghost" onClick={onCancel} disabled={pending}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ConsignmentPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<SettlementStatus>("PENDING");
  const [phoneInput, setPhoneInput] = useState("");
  const [phone, setPhone] = useState("");
  const [page, setPage] = useState(0);
  const [paying, setPaying] = useState<Settlement | null>(null);
  const [payError, setPayError] = useState<string | null>(null);

  const cashSession = useQuery({
    queryKey: ["cash-session", "current"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/cash-sessions/current");
      if (response.status === 200) return data ?? null;
      throw new Error(extractDetail(error) ?? "讀取開帳狀態失敗");
    },
  });

  const settlements = useQuery({
    queryKey: ["consignment", "settlements", status, phone, page],
    queryFn: async () => {
      const query: { status: SettlementStatus; limit: number; offset: number; phone?: string } = {
        status,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      };
      if (phone) query.phone = phone;
      const { data, error } = await api.GET("/api/v1/consignment/settlements", {
        params: { query },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取寄售結算失敗");
      return data;
    },
  });

  const drawerOpen = cashSession.isSuccess ? cashSession.data !== null : false;
  const rows = settlements.data ?? [];
  const totalPending = rows
    .filter((row) => row.status === "PENDING")
    .reduce((sum, row) => sum + (parseNtd(row.payout_amount) ?? 0), 0);

  const pay = useMutation({
    mutationFn: async (row: Settlement) => {
      const { data, error } = await api.POST(
        "/api/v1/consignment/settlements/{settlement_id}/pay",
        {
          params: {
            path: { settlement_id: row.id },
            header: { "Idempotency-Key": newIdempotencyKey() },
          },
        },
      );
      if (!data) throw new Error(extractDetail(error) ?? "付款失敗，請確認開帳狀態後重試");
      return data;
    },
    onSuccess: () => {
      setPaying(null);
      setPayError(null);
      void queryClient.invalidateQueries({ queryKey: ["consignment", "settlements"] });
      void queryClient.invalidateQueries({ queryKey: ["cash-session", "current"] });
    },
    onError: (err: Error) => setPayError(err.message),
  });

  return (
    <section className="settle-page">
      <div className="settle-head">
        <div>
          <h1 className="page-title">寄售付款</h1>
          <p className="hint">確認待撥款項，付款成功後再交付現金。</p>
        </div>
        <div className={`settle-drawer ${drawerOpen ? "settle-drawer-open" : "settle-drawer-closed"}`}>
          <span>{drawerOpen ? "開帳中" : "未開帳"}</span>
          {!drawerOpen && <Link href="/cash">前往開帳</Link>}
        </div>
      </div>

      {!drawerOpen && <p className="form-error">請先到現金對帳開帳後再付款。</p>}

      <div className="settle-tabs" aria-label="寄售結算狀態">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.status}
            type="button"
            className={`chip ${status === tab.status ? "chip-active" : ""}`}
            onClick={() => {
              setStatus(tab.status);
              setPage(0);
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <form
        className="settle-search"
        onSubmit={(e) => {
          e.preventDefault();
          setPhone(phoneInput.trim());
          setPage(0);
        }}
      >
        <input
          className="settle-search-input"
          inputMode="tel"
          placeholder="以寄售人手機查找"
          aria-label="以寄售人手機查找"
          value={phoneInput}
          onChange={(e) => setPhoneInput(e.target.value)}
        />
        <button type="submit" className="btn-secondary settle-search-submit">
          查找
        </button>
        {phone && (
          <button
            type="button"
            className="btn-ghost settle-search-clear"
            onClick={() => {
              setPhoneInput("");
              setPhone("");
              setPage(0);
            }}
          >
            清除（手機：{phone}）
          </button>
        )}
      </form>

      {status === "PENDING" && (
        <div className="member-banner">
          本頁待付款合計：<span className="money">{formatNtd(totalPending)}</span>
        </div>
      )}

      <div className="card settle-card">
        {settlements.isPending ? (
          <p>載入中…</p>
        ) : settlements.isError ? (
          <p role="alert" className="form-error">
            {settlements.error.message}
          </p>
        ) : rows.length === 0 ? (
          <p className="empty-state">目前沒有{STATUS_LABELS[status]}的寄售結算。</p>
        ) : (
          <div className="settle-table-wrap">
            <table className="data-table settle-table">
              <thead>
                <tr>
                  <th>寄售人</th>
                  <th>商品</th>
                  <th>售出</th>
                  <th>售價</th>
                  <th>抽成</th>
                  <th>應付</th>
                  <th>狀態</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>
                      {row.consignor_name ?? "—"}
                      {row.consignor_phone && <span className="row-sub">{row.consignor_phone}</span>}
                    </td>
                    <td>
                      {row.item_name ?? "—"}
                      <span className="row-sub">{row.item_code ?? `#${row.serialized_item_id}`}</span>
                    </td>
                    <td>
                      #{row.sale_id}
                      <span className="row-sub">{dt(row.sale_created_at ?? row.created_at)}</span>
                    </td>
                    <td className="money">{money(row.gross)}</td>
                    <td>
                      <span className="money">{money(row.commission_amount)}</span>
                      <span className="row-sub">{row.commission_pct}%</span>
                    </td>
                    <td className="money settle-payout">{money(row.payout_amount)}</td>
                    <td>
                      <SettlementStatusBadge row={row} /> <ReclaimFlag row={row} />
                    </td>
                    <td className="settle-actions">
                      {row.status === "PENDING" && (
                        <button
                          type="button"
                          className="btn-primary"
                          disabled={!drawerOpen || pay.isPending}
                          onClick={() => {
                            setPaying(row);
                            setPayError(null);
                          }}
                        >
                          付款
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!settlements.isPending && !settlements.isError && (
          <Pagination page={page} count={rows.length} pageSize={PAGE_SIZE} onPage={setPage} />
        )}
      </div>

      {paying !== null && (
        <ConfirmDialog
          row={paying}
          pending={pay.isPending}
          error={payError}
          onCancel={() => {
            setPaying(null);
            setPayError(null);
          }}
          onConfirm={() => pay.mutate(paying)}
        />
      )}
    </section>
  );
}
