"use client";
// F6.5 作廢收購確認對話框（限 MANAGER 觸發；後端 ManagerDep 為最終權威）：填原因 → 二次確認 →
// POST /acquisitions/{id}/void。作廢對稱反轉庫存/現金/購物金且無法復原，故需明確原因與確認。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { errorDetail, voidErrorMessage } from "@/features/acquisition/void";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type VoidResult = components["schemas"]["AcquisitionVoidResult"];

export function VoidConfirmDialog({
  acquisitionId,
  onClose,
  onVoided,
}: {
  acquisitionId: number;
  onClose: () => void;
  onVoided: (result: VoidResult) => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const voidMut = useMutation({
    mutationFn: async () => {
      const { data, error: apiErr, response } = await api.POST(
        "/api/v1/acquisitions/{acquisition_id}/void",
        {
          params: { path: { acquisition_id: acquisitionId } },
          body: { reason: reason.trim() },
        },
      );
      if (!data) throw new Error(voidErrorMessage(response.status, errorDetail(apiErr)));
      return data;
    },
    onSuccess: (data) => {
      // 作廢退現會在當前開帳 session 落 ACQUISITION_VOID_IN；庫存/狀態亦變動 → 失效相關快取。
      void queryClient.invalidateQueries({ queryKey: ["cash-session"] });
      void queryClient.invalidateQueries({ queryKey: ["acquisition", acquisitionId] });
      onVoided(data);
    },
    onError: (e: Error) => setError(e.message),
  });

  const reasonValid = reason.trim().length > 0;

  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="作廢收購確認">
      <div className="card pos-dialog acq-void-dialog">
        <h2>作廢收購單 #{acquisitionId}？</h2>
        <p className="hint">作廢將對稱反轉庫存、現金與購物金並留稽核，無法復原。請填寫原因。</p>
        <label className="field">
          <span className="field-label">作廢原因</span>
          <textarea
            aria-label="作廢原因"
            className="acq-void-reason"
            value={reason}
            maxLength={500}
            rows={3}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="button" className="btn-ghost" onClick={onClose} disabled={voidMut.isPending}>
            取消
          </button>
          <button
            type="button"
            className="btn-danger"
            disabled={!reasonValid || voidMut.isPending}
            onClick={() => {
              setError(null);
              voidMut.mutate();
            }}
          >
            確認作廢
          </button>
        </div>
      </div>
    </div>
  );
}
