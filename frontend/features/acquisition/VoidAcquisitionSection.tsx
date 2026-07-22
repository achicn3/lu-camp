"use client";
// F6.5 作廢收購查詢區（限 MANAGER 顯示；後端 ManagerDep 為最終權威）：輸入收購單號 → 查詢摘要 →
// 可作廢者開啟確認對話框。has-sold／credit-spent 無法前端判定，於送出後由後端 409 回報。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ACQ_TYPE_LABEL, PAYOUT_LABEL } from "@/features/acquisition/labels";
import { canVoid, errorDetail, voidBlockReason } from "@/features/acquisition/void";
import { VoidConfirmDialog } from "@/features/acquisition/VoidConfirmDialog";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type VoidResult = components["schemas"]["AcquisitionVoidResult"];

function ntd(value: string | null): string {
  if (value === null) return "—";
  return formatNtd(parseNtd(value) ?? 0);
}

export function VoidAcquisitionSection() {
  const queryClient = useQueryClient();
  const [idInput, setIdInput] = useState("");
  const [queryId, setQueryId] = useState<number | null>(null);
  const [inputError, setInputError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [voidResult, setVoidResult] = useState<VoidResult | null>(null);

  const acqQuery = useQuery({
    queryKey: ["acquisition", queryId],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/acquisitions/{acquisition_id}", {
        params: { path: { acquisition_id: queryId as number } },
      });
      if (!data) {
        throw new Error(
          response.status === 404
            ? "找不到收購單（單號可能有誤）"
            : (errorDetail(error) ?? "查詢失敗，請稍後再試"),
        );
      }
      return data;
    },
    enabled: queryId !== null,
    retry: false,
  });

  function onLookup() {
    // 作廢屬破壞性操作、以輸入單號為鍵：拒絕部分解析（如 "12abc"→12 會誤指他單），
    // 僅接受純數字且 > 0 的單號。
    const trimmed = idInput.trim();
    setVoidResult(null);
    if (!/^\d+$/.test(trimmed) || Number(trimmed) <= 0) {
      setQueryId(null);
      setInputError("請輸入有效的收購單號（純數字）");
      return;
    }
    setInputError(null);
    setQueryId(Number.parseInt(trimmed, 10));
  }

  const acq = acqQuery.data ?? null;
  const blockReason = acq !== null ? voidBlockReason(acq) : null;

  return (
    <div className="card acq-void-section">
      <h2>作廢收購（限管理者）</h2>
      <p className="hint">輸入收購單號查詢後可作廢；作廢將對稱反轉庫存、現金與購物金並留稽核。</p>
      <form
        className="acq-void-lookup"
        onSubmit={(e) => {
          e.preventDefault();
          onLookup();
        }}
      >
        <label className="field">
          <span className="field-label">收購單號</span>
          <input
            aria-label="收購單號"
            inputMode="numeric"
            value={idInput}
            onChange={(e) => setIdInput(e.target.value)}
          />
        </label>
        <button type="submit" className="btn-ghost" disabled={acqQuery.isFetching}>
          查詢
        </button>
      </form>

      {inputError !== null && (
        <p role="alert" className="form-error">
          {inputError}
        </p>
      )}

      {acqQuery.isError && (
        <p role="alert" className="form-error">
          {(acqQuery.error as Error).message}
        </p>
      )}

      {acq !== null && (
        <div className="acq-void-summary">
          <dl className="stat-list">
            <div>
              <dt>單號</dt>
              <dd>#{acq.id}</dd>
            </div>
            <div>
              <dt>類型</dt>
              <dd>{ACQ_TYPE_LABEL[acq.type]}</dd>
            </div>
            <div>
              <dt>撥款方式</dt>
              <dd>{PAYOUT_LABEL[acq.payout_method]}</dd>
            </div>
            <div>
              <dt>現金撥付</dt>
              <dd className="money">{ntd(acq.payout_cash_amount)}</dd>
            </div>
            <div>
              <dt>購物金入帳</dt>
              <dd className="money">{ntd(acq.payout_credit_cash_equivalent)}</dd>
            </div>
            <div>
              <dt>建立時間</dt>
              <dd>{formatTaipeiDateTime(acq.created_at)}</dd>
            </div>
            {acq.voided_at !== null && (
              <div>
                <dt>作廢時間</dt>
                <dd>{formatTaipeiDateTime(acq.voided_at)}</dd>
              </div>
            )}
          </dl>

          {voidResult === null && canVoid(acq) && (
            <button type="button" className="btn-danger" onClick={() => setDialogOpen(true)}>
              作廢收購
            </button>
          )}

          {voidResult === null && blockReason !== null && (
            <p className="form-error">{blockReason}</p>
          )}
        </div>
      )}

      {voidResult !== null && (
        <div className="card form-success acq-void-result">
          <p>已作廢收購單 #{voidResult.acquisition_id}。</p>
          <p>退回現金：<strong className="money">{ntd(voidResult.reversed_cash)}</strong></p>
          <p>沖回購物金：<strong className="money">{ntd(voidResult.reversed_credit)}</strong></p>
        </div>
      )}

      {dialogOpen && acq !== null && (
        <VoidConfirmDialog
          acquisitionId={acq.id}
          onClose={() => setDialogOpen(false)}
          onVoided={(result) => {
            setDialogOpen(false);
            setVoidResult(result);
            void queryClient.invalidateQueries({ queryKey: ["acquisition", acq.id] });
          }}
        />
      )}
    </div>
  );
}
