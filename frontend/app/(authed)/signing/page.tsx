"use client";
// /signing 簽署紀錄（docs/29 波次一）：店長/店員調閱簽署證據——任務清單（狀態/類型過濾）
// →內容快照＋手寫簽名影像＋綁定單據。裁示（2026-07-16）：調閱不寫稽核、隨時想調就調。
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  SIGNING_KIND_LABELS,
  SIGNING_PAYOUT_LABELS,
  SIGNING_STATUS_LABELS,
  contentRows,
  refLabel,
} from "@/features/signing/labels";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { getToken } from "@/lib/token";

type SignatureTask = components["schemas"]["SignatureTaskRead"];

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const PAGE_SIZE = 20;

function timeLabel(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** 簽名影像需帶 Bearer token 取回（img src 無法帶標頭）→ blob URL，卸載時釋放。
 *  以 (taskId, url) 配對存放並於回傳時比對，避免 effect 內同步 setState 與跨任務殘影。 */
function useSignatureImage(taskId: number | null, hasSignature: boolean): string | null {
  const [img, setImg] = useState<{ id: number; url: string } | null>(null);
  useEffect(() => {
    if (taskId == null || !hasSignature) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    (async () => {
      const token = getToken();
      const res = await fetch(`${API_BASE}/api/v1/signing/tasks/${taskId}/signature`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      }).catch(() => null);
      if (!res || !res.ok || cancelled) return;
      objectUrl = URL.createObjectURL(await res.blob());
      if (!cancelled) setImg({ id: taskId, url: objectUrl });
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [taskId, hasSignature]);
  return img !== null && img.id === taskId ? img.url : null;
}

function TaskDetailDialog({ task, onClose }: { task: SignatureTask; onClose: () => void }) {
  const imageUrl = useSignatureImage(task.id, task.has_signature);
  const rows = contentRows(task.content as Record<string, unknown>);
  const ref = refLabel(task.kind, task.ref_type ?? null, task.ref_id ?? null);
  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="簽署證據">
      <div className="card pos-dialog" style={{ maxWidth: 560 }}>
        <h2>
          {SIGNING_KIND_LABELS[task.kind] ?? task.kind} #{task.id}
        </h2>
        <p>
          狀態：{SIGNING_STATUS_LABELS[task.status] ?? task.status}
          {task.signed_at ? `｜簽署於 ${timeLabel(task.signed_at)}` : ""}
          {task.chosen_payout
            ? `｜撥款 ${SIGNING_PAYOUT_LABELS[task.chosen_payout] ?? task.chosen_payout}`
            : ""}
        </p>
        {ref ? <p>綁定單據：{ref}</p> : null}
        {task.agreement_version != null ? <p>切結書版本：v{task.agreement_version}</p> : null}
        <h3>簽署當下內容快照</h3>
        <table className="data-table">
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <th scope="row">{r.label}</th>
                <td>{r.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3>手寫簽名</h3>
        {task.has_signature ? (
          imageUrl ? (
            // eslint-disable-next-line @next/next/no-img-element -- blob URL 無法用 next/image
            <img
              src={imageUrl}
              alt={`任務 ${task.id} 簽名影像`}
              style={{ maxWidth: "100%", border: "1px solid var(--border, #ccc)", background: "#fff" }}
            />
          ) : (
            <p>簽名影像載入中…</p>
          )
        ) : (
          <p>此任務無簽名（未完成簽署）。</p>
        )}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            關閉
          </button>
        </div>
      </div>
    </div>
  );
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
    <main>
      <h1>簽署紀錄</h1>
      <p>調閱客人簽署證據：切結書、購物金扣抵確認、交易簽收（內容快照＋手寫簽名）。</p>
      <div className="filter-row" style={{ display: "flex", gap: 12, margin: "12px 0" }}>
        <label>
          狀態{" "}
          <select
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
        <label>
          類型{" "}
          <select
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
      {selected ? <TaskDetailDialog task={selected} onClose={() => setSelected(null)} /> : null}
    </main>
  );
}
