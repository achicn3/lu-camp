"use client";
// 庫存明細的「作廢這件」：只作廢這一件、退回它在收購單裡的那一份（店主 2026-10-02）。
//
// 走的是收購作廢的逐件路徑（POST /acquisitions/{id}/void 帶 item_ids=[這件]）：
// 現金／購物金按原付款比例只沖回這件的成本，同一張收購單的其他商品照常在庫、收購單仍有效。
// 只放在買斷序號品：寄售走寄售退貨；散裝只能整張作廢（後端也擋）。管理者限定由明細視窗把關，
// 後端 ManagerDep 為最終權威。
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { canVoidSingleItem } from "@/features/acquisition/void";
import { VoidConfirmDialog } from "@/features/acquisition/VoidConfirmDialog";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type SerializedDetail = components["schemas"]["SerializedItemDetailRead"];
type VoidResult = components["schemas"]["AcquisitionVoidResult"];

function ntd(value: string | null): string {
  return value === null ? "—" : formatNtd(parseNtd(value) ?? 0);
}

export function VoidSingleItemSection({
  item,
  acquisitionId,
}: {
  item: SerializedDetail;
  acquisitionId: number;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<VoidResult | null>(null);

  if (result === null && !canVoidSingleItem(item)) return null;

  return (
    <section className="inv-void-item" aria-label="作廢這件">
      <h4 className="inv-detail-subtitle">作廢這件</h4>
      {result === null ? (
        <>
          <p className="hint">
            這件屬於收購單 #{acquisitionId}。只作廢這一件：按原付款比例沖回這件（收購價{" "}
            <strong className="money">{ntd(item.acquisition_cost)}</strong>
            ）的現金與購物金——現金收回錢櫃、購物金從賣方帳上沖回（含原溢價），其他商品不受影響。
          </p>
          <button type="button" className="btn-danger" onClick={() => setConfirming(true)}>
            作廢這件
          </button>
        </>
      ) : (
        <p role="status" className="form-success">
          已作廢這件（收購單 #{result.acquisition_id}）。退回現金{" "}
          <strong className="money">{ntd(result.reversed_cash)}</strong>、沖回購物金{" "}
          <strong className="money">{ntd(result.reversed_credit)}</strong>。
        </p>
      )}
      {confirming && (
        <VoidConfirmDialog
          acquisitionId={acquisitionId}
          itemIds={[item.id]}
          onClose={() => setConfirming(false)}
          onVoided={(voided) => {
            setConfirming(false);
            setResult(voided);
            void queryClient.invalidateQueries({ queryKey: ["inventory"] });
            void queryClient.invalidateQueries({ queryKey: ["serialized-detail", item.id] });
            void queryClient.invalidateQueries({ queryKey: ["acquisitions"] });
          }}
        />
      )}
    </section>
  );
}
