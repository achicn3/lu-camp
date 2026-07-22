"use client";

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
import { formatTaipeiDateTime } from "@/lib/datetime";
import { getToken } from "@/lib/token";

type SignatureTask = components["schemas"]["SignatureTaskRead"];

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function timeLabel(iso: string | null | undefined): string {
  return formatTaipeiDateTime(iso);
}

/** 簽名影像需帶 Bearer token 取回（img src 無法帶標頭）→ blob URL，卸載時釋放。 */
function useSignatureImage(
  taskId: number,
  hasSignature: boolean,
  retryKey: number,
): { url: string | null; error: string | null } {
  const [img, setImg] = useState<{ id: number; url?: string; error?: string } | null>(null);
  useEffect(() => {
    if (!hasSignature) return;
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
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setImg({ id: taskId, url: objectUrl });
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

export function SignatureEvidenceDialog({
  taskId,
  initialTask,
  onClose,
}: {
  taskId: number;
  initialTask?: SignatureTask;
  onClose: () => void;
}) {
  const [retryKey, setRetryKey] = useState(0);
  const detail = useQuery({
    queryKey: ["signing-task-detail", taskId],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/signing/tasks/{task_id}", {
        params: { path: { task_id: taskId } },
      });
      if (!data) throw new Error(String(error ?? "載入失敗"));
      return data;
    },
  });
  const full = detail.data ?? initialTask;
  const { url: imageUrl, error: imageError } = useSignatureImage(
    taskId,
    full?.has_signature ?? false,
    retryKey,
  );
  const rows = full ? contentRows(full.content as Record<string, unknown>) : [];
  const ref = full
    ? refLabel(
        full.kind,
        full.ref_type ?? null,
        full.ref_id ?? null,
        full.bound_acquisition_id ?? null,
        full.bound_sale_id ?? null,
      )
    : null;

  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="簽署證據">
      <div
        className="card pos-dialog"
        style={{ maxWidth: 560, maxHeight: "85vh", overflowY: "auto" }}
      >
        {full ? (
          <>
            <h2>
              {SIGNING_KIND_LABELS[full.kind] ?? full.kind} #{full.id}
            </h2>
            <p>
              {full.signer_name ? `簽署人：${full.signer_name}｜` : ""}
              狀態：{SIGNING_STATUS_LABELS[full.status] ?? full.status}
              {full.signed_at ? `｜簽署於 ${timeLabel(full.signed_at)}` : ""}
              {full.chosen_payout
                ? `｜撥款 ${SIGNING_PAYOUT_LABELS[full.chosen_payout] ?? full.chosen_payout}`
                : ""}
            </p>
            {ref ? <p>綁定單據：{ref}</p> : null}
            {full.agreement_version != null ? (
              <p>切結書版本：v{full.agreement_version}</p>
            ) : null}
            {full.agreement_body ? (
              <details>
                <summary>
                  切結書全文
                  {full.agreement_title ? `：${full.agreement_title}` : ""}（客人簽署的條款）
                </summary>
                <pre style={{ whiteSpace: "pre-wrap", fontSize: "0.85em", marginTop: 8 }}>
                  {full.agreement_body}
                </pre>
              </details>
            ) : null}
            <h3>簽署當下內容快照</h3>
            <table className="data-table">
              <tbody>
                {rows.map((row) => (
                  <tr key={row.label}>
                    <th scope="row">{row.label}</th>
                    <td>{row.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h3>手寫簽名</h3>
            {full.has_signature ? (
              imageUrl ? (
                // eslint-disable-next-line @next/next/no-img-element -- blob URL 無法用 next/image
                <img
                  src={imageUrl}
                  alt={`任務 ${full.id} 簽名影像`}
                  style={{
                    maxWidth: "100%",
                    border: "1px solid var(--border, #ccc)",
                    background: "#fff",
                  }}
                />
              ) : imageError ? (
                <p role="alert">
                  {imageError}{" "}
                  <button type="button" onClick={() => setRetryKey((key) => key + 1)}>
                    重試
                  </button>
                </p>
              ) : (
                <p>簽名影像載入中…</p>
              )
            ) : (
              <p>此任務無簽名（未完成簽署）。</p>
            )}
          </>
        ) : detail.isError ? (
          <>
            <h2>簽署證據 #{taskId}</h2>
            <p role="alert">
              證據載入失敗。{" "}
              <button type="button" onClick={() => void detail.refetch()}>
                重試
              </button>
            </p>
          </>
        ) : (
          <>
            <h2>簽署證據 #{taskId}</h2>
            <p>載入中…</p>
          </>
        )}
        {detail.isError && full ? (
          <p role="alert">
            證據明細載入失敗（綁定單據／最新狀態可能不完整）。{" "}
            <button type="button" onClick={() => void detail.refetch()}>
              重試
            </button>
          </p>
        ) : null}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            關閉
          </button>
        </div>
      </div>
    </div>
  );
}
