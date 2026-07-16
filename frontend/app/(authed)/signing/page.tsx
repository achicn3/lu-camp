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
 *  以 (taskId, url|error) 配對存放並於回傳時比對，避免 effect 內同步 setState 與跨任務殘影；
 *  失敗必須成為可見錯誤態，不可永遠「載入中」（Codex P2）。 */
function useSignatureImage(
  taskId: number | null,
  hasSignature: boolean,
  retryKey: number,
): { url: string | null; error: string | null } {
  const [img, setImg] = useState<{ id: number; url?: string; error?: string } | null>(null);
  useEffect(() => {
    if (taskId == null || !hasSignature) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    (async () => {
      try {
        const token = getToken();
        const res = await fetch(`${API_BASE}/api/v1/signing/tasks/${taskId}/signature`, {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        });
        if (cancelled) return;
        if (!res.ok) {
          setImg({ id: taskId, error: `影像載入失敗（HTTP ${res.status}）` });
          return;
        }
        objectUrl = URL.createObjectURL(await res.blob());
        if (!cancelled) setImg({ id: taskId, url: objectUrl });
      } catch {
        if (!cancelled) setImg({ id: taskId, error: "影像載入失敗（連線錯誤）" });
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [taskId, hasSignature, retryKey]);
  if (img === null || img.id !== taskId) return { url: null, error: null };
  return { url: img.url ?? null, error: img.error ?? null };
}

function TaskDetailDialog({ task, onClose }: { task: SignatureTask; onClose: () => void }) {
  const [retryKey, setRetryKey] = useState(0);
  // 清單列缺反向綁定（避免 N+1）→ 開啟時取單筆 detail 回填 bound_*（Codex P1）
  const detail = useQuery({
    queryKey: ["signing-task-detail", task.id],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/signing/tasks/{task_id}", {
        params: { path: { task_id: task.id } },
      });
      if (!data) throw new Error(String(error ?? "載入失敗"));
      return data;
    },
  });
  // 一律以 detail 的**當前實態**渲染（清單快照可能已過期：待簽→已簽/作廢；Codex 第二輪 P2）
  const full = detail.data ?? task;
  const { url: imageUrl, error: imageError } = useSignatureImage(
    full.id,
    full.has_signature,
    retryKey,
  );
  const rows = contentRows(full.content as Record<string, unknown>);
  const ref = refLabel(
    full.kind,
    full.ref_type ?? null,
    full.ref_id ?? null,
    full.bound_acquisition_id ?? null,
    full.bound_sale_id ?? null,
  );
  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="簽署證據">
      <div
        className="card pos-dialog"
        style={{ maxWidth: 560, maxHeight: "85vh", overflowY: "auto" }}
      >
        <h2>
          {SIGNING_KIND_LABELS[full.kind] ?? full.kind} #{full.id}
        </h2>
        <p>
          狀態：{SIGNING_STATUS_LABELS[full.status] ?? full.status}
          {full.signed_at ? `｜簽署於 ${timeLabel(full.signed_at)}` : ""}
          {full.chosen_payout
            ? `｜撥款 ${SIGNING_PAYOUT_LABELS[full.chosen_payout] ?? full.chosen_payout}`
            : ""}
        </p>
        {ref ? <p>綁定單據：{ref}</p> : null}
        {full.agreement_version != null ? <p>切結書版本：v{full.agreement_version}</p> : null}
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
        {detail.isError ? (
          <p role="alert">
            證據明細載入失敗（綁定單據/最新狀態可能不完整）。{" "}
            <button type="button" onClick={() => void detail.refetch()}>
              重試
            </button>
          </p>
        ) : null}
        {full.has_signature ? (
          imageUrl ? (
            // eslint-disable-next-line @next/next/no-img-element -- blob URL 無法用 next/image
            <img
              src={imageUrl}
              alt={`任務 ${full.id} 簽名影像`}
              style={{ maxWidth: "100%", border: "1px solid var(--border, #ccc)", background: "#fff" }}
            />
          ) : imageError ? (
            <p role="alert">
              {imageError}{" "}
              <button type="button" onClick={() => setRetryKey((k) => k + 1)}>
                重試
              </button>
            </p>
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
    <section>
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
    </section>
  );
}
