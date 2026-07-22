"use client";
// /signing 簽署紀錄（docs/29 波次一）：店長/店員調閱簽署證據——任務清單（狀態/類型過濾）
// →內容快照＋手寫簽名影像＋綁定單據。裁示（2026-07-16）：調閱不寫稽核、隨時想調就調。
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import {
  SIGNING_KIND_LABELS,
  SIGNING_PAYOUT_LABELS,
  SIGNING_STATUS_LABELS,
} from "@/features/signing/labels";
import { SignatureEvidenceDialog } from "@/features/signing/SignatureEvidenceDialog";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";

type SignatureTask = components["schemas"]["SignatureTaskRead"];

const PAGE_SIZE = 20;

function timeLabel(iso: string | null | undefined): string {
  return formatTaipeiDateTime(iso);
}

export default function SigningPage() {
  const [statusFilter, setStatusFilter] = useState<string>("SIGNED");
  const [kindFilter, setKindFilter] = useState<string>("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<SignatureTask | null>(null);

  const query = useQuery({
    queryKey: ["signing-tasks", statusFilter, kindFilter, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/signing/tasks", {
        params: {
          query: {
            ...(statusFilter ? { status: statusFilter as SignatureTask["status"] } : {}),
            ...(kindFilter ? { kind: kindFilter as SignatureTask["kind"] } : {}),
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          },
        },
      });
      if (!data) throw new Error(String(error ?? "載入失敗"));
      return data;
    },
  });

  const tasks = query.data ?? [];
  return (
    <section>
      <h1>簽署紀錄</h1>
      <p>調閱客人簽署證據：切結書、購物金扣抵確認、交易簽收（內容快照＋手寫簽名）。</p>
      <div className="signing-filters" aria-label="簽署紀錄篩選">
        <label className="signing-filter">
          <span>狀態</span>
          <select
            className="signing-filter-select"
            aria-label="狀態"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(0);
            }}
          >
            <option value="">全部</option>
            {Object.entries(SIGNING_STATUS_LABELS).map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="signing-filter">
          <span>類型</span>
          <select
            className="signing-filter-select"
            aria-label="類型"
            value={kindFilter}
            onChange={(e) => {
              setKindFilter(e.target.value);
              setPage(0);
            }}
          >
            <option value="">全部</option>
            {Object.entries(SIGNING_KIND_LABELS).map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {query.isLoading ? <p>載入中…</p> : null}
      {query.isError ? <p role="alert">載入失敗，請重試。</p> : null}
      {!query.isLoading && tasks.length === 0 ? <p>目前沒有符合條件的簽署紀錄。</p> : null}
      {tasks.length > 0 ? (
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>類型</th>
              <th>狀態</th>
              <th>簽署時間</th>
              <th>撥款</th>
              <th>證據</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.id}>
                <td>{t.id}</td>
                <td>{SIGNING_KIND_LABELS[t.kind] ?? t.kind}</td>
                <td>{SIGNING_STATUS_LABELS[t.status] ?? t.status}</td>
                <td>{timeLabel(t.signed_at)}</td>
                <td>{t.chosen_payout ? (SIGNING_PAYOUT_LABELS[t.chosen_payout] ?? t.chosen_payout) : "—"}</td>
                <td>
                  <button type="button" onClick={() => setSelected(t)}>
                    查看
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <div className="pager" style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button type="button" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
          上一頁
        </button>
        <span>第 {page + 1} 頁</span>
        <button
          type="button"
          disabled={tasks.length < PAGE_SIZE}
          onClick={() => setPage((p) => p + 1)}
        >
          下一頁
        </button>
      </div>
      {selected ? (
        <SignatureEvidenceDialog
          taskId={selected.id}
          initialTask={selected}
          onClose={() => setSelected(null)}
        />
      ) : null}
    </section>
  );
}
