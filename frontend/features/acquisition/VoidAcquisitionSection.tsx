"use client";
// F6.5 作廢收購查詢區（限 MANAGER 顯示；後端 ManagerDep 為最終權威）：輸入收購單號 → 查詢摘要 →
// 可作廢者開啟確認對話框。has-sold／credit-spent 無法前端判定，於送出後由後端 409 回報。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ACQ_TYPE_LABEL, PAYOUT_LABEL } from "@/features/acquisition/labels";
import {
  canVoid,
  errorDetail,
  isVoidableItemStatus,
  voidBlockReason,
} from "@/features/acquisition/void";
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

export function VoidAcquisitionSection({ acquisitionId, onVoided, onClose, preselectAll = false }: {
  acquisitionId?: number;
  onVoided?: (result: VoidResult) => void;
  onClose?: () => void;
  /** 一開始就勾好所有可作廢的商品（收購紀錄的「作廢」＝預設整張，取消勾選的保留）。 */
  preselectAll?: boolean;
} = {}) {
  const queryClient = useQueryClient();
  const [idInput, setIdInput] = useState("");
  const [queryId, setQueryId] = useState<number | null>(acquisitionId ?? null);
  const [inputError, setInputError] = useState<string | null>(null);
  // null＝「全部可作廢的」（preselectAll 的初始狀態；商品清單載入前無從列舉 id）。
  const [selectedIds, setSelectedIds] = useState<number[] | null>(preselectAll ? null : []);
  // 確認視窗開著時送出的商品＝**打開那一刻**勾的（null＝沒開）。直接傳即時勾選的話，背景重新整理
  // 讓某件不再可作廢時，送出件數會在店長沒看到的情況下改變。
  const [confirmIds, setConfirmIds] = useState<number[] | null>(null);
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

  const itemsQuery = useQuery({
    queryKey: ["acquisition-void-items", queryId],
    enabled: acqQuery.data?.type === "BUYOUT",
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/acquisitions/{acquisition_id}/void-items", {
        params: { path: { acquisition_id: queryId as number } },
      });
      if (!data) throw new Error(errorDetail(error) ?? "無法讀取商品清單");
      return data;
    },
  });
  const selectableIds = (itemsQuery.data ?? []).filter((item) => !item.voided && isVoidableItemStatus(item.status)).map((item) => item.id);
  const selected =
    selectedIds === null ? selectableIds : selectedIds.filter((id) => selectableIds.includes(id));

  function onLookup() {
    // 作廢屬破壞性操作、以輸入單號為鍵：拒絕部分解析（如 "12abc"→12 會誤指他單），
    // 僅接受純數字且 > 0 的單號。
    const trimmed = idInput.trim();
    setVoidResult(null);
    setSelectedIds([]);
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
      <p className="hint">
        {acquisitionId === undefined ? "輸入收購單號，" : ""}
        勾選要作廢的商品（沒勾的保留）。現金與購物金按原付款比例沖回，購物金含原溢價；散裝收購以整批作廢。
      </p>
      {acquisitionId === undefined && <form
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
      </form>}

      {onClose && <button type="button" className="btn-ghost" onClick={onClose}>關閉</button>}

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

          {acq.type === "BUYOUT" && (
            <fieldset className="acq-void-items">
              <legend>選擇作廢商品</legend>
              {itemsQuery.isPending && <p role="status">正在讀取商品…</p>}
              {itemsQuery.isError && <p role="alert">{itemsQuery.error.message}</p>}
              {selectableIds.length > 0 && <label>
                <input type="checkbox" checked={selected.length === selectableIds.length}
                  onChange={(event) => setSelectedIds(event.target.checked ? selectableIds : [])} /> 全選可作廢商品
              </label>}
              {(itemsQuery.data ?? []).map((item) => <label key={item.id}>
                <input type="checkbox" checked={selected.includes(item.id)}
                  disabled={!selectableIds.includes(item.id)}
                  onChange={(event) => setSelectedIds(event.target.checked ? [...selected, item.id] : selected.filter((id) => id !== item.id))} />
                <span>{item.name} · {item.item_code} · 收購價 {ntd(item.acquisition_cost)}
                {item.voided ? " · 已作廢" : !isVoidableItemStatus(item.status) ? " · 已售出或下架，不可作廢" : ""}</span>
              </label>)}
            </fieldset>
          )}
          {canVoid(acq) && (
            <button type="button" className="btn-danger" onClick={() => setConfirmIds(selected)}
              disabled={acq.type === "BUYOUT" && (itemsQuery.isFetching || selected.length === 0)}>
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
          <p>{voidResult.fully_voided ? "已作廢收購單" : "已作廢所選商品，其餘商品保留；收購單"} #{voidResult.acquisition_id}。</p>
          <p>退回現金：<strong className="money">{ntd(voidResult.reversed_cash)}</strong></p>
          <p>沖回購物金：<strong className="money">{ntd(voidResult.reversed_credit)}</strong></p>
        </div>
      )}

      {confirmIds !== null && acq !== null && (
        <VoidConfirmDialog
          acquisitionId={acq.id}
          itemIds={acq.type === "BUYOUT" ? confirmIds : undefined}
          onClose={() => setConfirmIds(null)}
          onVoided={(result) => {
            setConfirmIds(null);
            setVoidResult(result);
            onVoided?.(result);
            setSelectedIds([]);
            void queryClient.invalidateQueries({ queryKey: ["acquisition-void-items", acq.id] });
            void queryClient.invalidateQueries({ queryKey: ["acquisition", acq.id] });
          }}
        />
      )}
    </div>
  );
}
