"use client";
// 收購紀錄「改撥款方式」（店主 2026-10-10）：客人反悔，購物金 ↔ 現金，一顆按鈕兩個方向。
// 購物金 → 現金：購物金（含溢價）整筆扣回、從抽屜付溢價前的價值。
// 現金 → 購物金：客人把現金還回抽屜、照目前設定的溢價率撥購物金（要是會員）。
// 商品不動、客人不重簽（裁示）。能不能改的最終判斷在後端（購物金已花掉、沒開帳、撥過購物金又要改回購物金等）。
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { creditPremiumPreview } from "@/features/acquisition/pricing";
import { errorDetail } from "@/features/acquisition/void";
import { ConfirmDialog } from "@/features/common/ConfirmDialog";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Row = components["schemas"]["AcquisitionListItem"];
type Target = "CASH" | "STORE_CREDIT";

const TARGET_LABEL: Record<Target, string> = { CASH: "現金", STORE_CREDIT: "購物金" };

function amount(value: string | null | undefined): number {
  return value == null ? 0 : (parseNtd(value) ?? 0);
}

function ntd(value: number): string {
  return `$${formatNtd(value)}`;
}

/** 能改成什麼：照後端清單給的 payout_change_to（撥過購物金又改回現金的單，後端才知道不能再改）。 */
export function payoutChangeTarget(row: Row): Target | null {
  const to = row.payout_change_to;
  return to === "CASH" || to === "STORE_CREDIT" ? to : null;
}

function doneMessage(result: components["schemas"]["AcquisitionPayoutChangeResult"]): string {
  const cash = ntd(amount(result.cash));
  const credit = ntd(amount(result.store_credit));
  return result.payout_method === "CASH"
    ? `收購單 #${result.acquisition_id} 已改成現金：請從抽屜拿現金 ${cash} 給客人；客人的購物金已扣回 ${credit}。`
    : `收購單 #${result.acquisition_id} 已改成購物金：請向客人收回現金 ${cash} 放進抽屜；已撥給客人購物金 ${credit}。`;
}

export function ChangePayoutAction({
  row,
  onDone,
  onError,
}: {
  row: Row;
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const target = payoutChangeTarget(row);
  const toCredit = target === "STORE_CREDIT";
  // 改成購物金才需要溢價率試算；購物金入帳以後端當下設定為準（送出後的提示用後端回的金額）。
  const premium = useQuery({
    queryKey: ["settings", "premium-rate"],
    queryFn: async () => (await api.GET("/api/v1/settings")).data?.premium_rate ?? null,
    enabled: confirming && toCredit,
  });

  const change = useMutation({
    mutationFn: async (to: Target) => {
      const { data, error } = await api.POST(
        "/api/v1/acquisitions/{acquisition_id}/change-payout",
        { params: { path: { acquisition_id: row.id } }, body: { payout_method: to } },
      );
      if (!data) throw new Error(errorDetail(error) ?? "改撥款方式失敗");
      return data;
    },
    onSuccess: (result) => {
      setConfirming(false);
      onDone(doneMessage(result));
    },
    onError: (e: Error) => {
      setConfirming(false);
      onError(e.message);
    },
  });

  if (target === null) return null;
  const cash = toCredit ? amount(row.payout_cash_amount) : amount(row.payout_credit_cash_equivalent);
  const credit =
    toCredit && premium.data != null ? cash + creditPremiumPreview(cash, premium.data) : null;

  return (
    <>
      <button type="button" className="btn-secondary" onClick={() => setConfirming(true)}>
        改撥款方式
      </button>
      {confirming && (
        <ConfirmDialog
          title="改撥款方式"
          confirmLabel={`確定改成${TARGET_LABEL[target]}`}
          busy={change.isPending}
          body={
            <>
              <p>
                收購單 #{row.id}（{row.seller_name || "賣方"}）：
                <strong>{toCredit ? "現金 → 購物金" : "購物金 → 現金"}</strong>
              </p>
              {toCredit ? (
                <ul>
                  <li>
                    客人把當初拿的現金 <strong className="money">{ntd(cash)}</strong> 還回抽屜。
                  </li>
                  <li>
                    撥給客人購物金{" "}
                    <strong className="money">{credit === null ? "（試算中…）" : ntd(credit)}</strong>
                    （照目前設定的溢價率；客人要是會員）。
                  </li>
                  <li>購物金只能撥一次：之後若客人又要現金可以再改回，但不能再改成購物金。</li>
                </ul>
              ) : (
                <ul>
                  <li>
                    從抽屜付現金 <strong className="money">{ntd(cash)}</strong> 給客人（溢價前的價值）。
                  </li>
                  <li>當初撥的購物金（含溢價）全數從客人帳戶扣回；客人已經花掉一部分就不能改。</li>
                  <li>改成現金後，就不能再改回購物金。</li>
                </ul>
              )}
              <p className="hint">商品不動，客人不用重新簽名；系統會記下誰改的。</p>
            </>
          }
          onConfirm={() => change.mutate(target)}
          onCancel={() => setConfirming(false)}
        />
      )}
    </>
  );
}
