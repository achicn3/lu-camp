"use client";
// 收購紀錄「改成付現」（店主 2026-10-10）：客人選了購物金、送出後反悔要現金。
// 當初撥的購物金（含溢價）全數扣回、從抽屜付出溢價前的價值；商品不動、客人不重簽（裁示）。
// 只限全額購物金撥款、沒作廢的單；能不能改的最終判斷在後端（購物金已花掉、沒開帳等）。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { errorDetail } from "@/features/acquisition/void";
import { ConfirmDialog } from "@/features/common/ConfirmDialog";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Row = components["schemas"]["AcquisitionListItem"];

function amount(value: string | null | undefined): number {
  return value == null ? 0 : (parseNtd(value) ?? 0);
}

/** 全額購物金撥款、沒作廢的收購單才能改成付現（寄售當下不付錢）。 */
export function canConvertToCash(row: Row): boolean {
  return (
    row.voided_at == null &&
    row.type !== "CONSIGNMENT" &&
    row.payout_method === "STORE_CREDIT" &&
    amount(row.payout_cash_amount) === 0 &&
    amount(row.payout_credit_cash_equivalent) > 0
  );
}

export function ConvertToCashAction({
  row,
  onDone,
  onError,
}: {
  row: Row;
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const cash = amount(row.payout_credit_cash_equivalent);

  const convert = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST(
        "/api/v1/acquisitions/{acquisition_id}/convert-payout-to-cash",
        { params: { path: { acquisition_id: row.id } } },
      );
      if (!data) throw new Error(errorDetail(error) ?? "改成付現失敗");
      return data;
    },
    onSuccess: (result) => {
      setConfirming(false);
      onDone(
        `收購單 #${result.acquisition_id} 已改成付現：請從抽屜拿現金 $${formatNtd(amount(result.cash_paid))} 給客人；` +
          `客人的購物金已扣回 $${formatNtd(amount(result.reversed_credit))}。`,
      );
    },
    onError: (e: Error) => {
      setConfirming(false);
      onError(e.message);
    },
  });

  return (
    <>
      <button type="button" className="btn-secondary" onClick={() => setConfirming(true)}>
        改成付現
      </button>
      {confirming && (
        <ConfirmDialog
          title="改成付現"
          confirmLabel="確定改成付現"
          busy={convert.isPending}
          body={
            <>
              <p>
                收購單 #{row.id}（{row.seller_name || "賣方"}）原本撥購物金。改成付現後：
              </p>
              <ul>
                <li>
                  從抽屜付現金 <strong className="money">${formatNtd(cash)}</strong> 給客人（溢價前的價值）。
                </li>
                <li>當初撥的購物金（含溢價）全數從客人帳戶扣回；客人已經花掉一部分就不能改。</li>
                <li>商品不動，客人不用重新簽名；系統會記下誰改的。</li>
              </ul>
            </>
          }
          onConfirm={() => convert.mutate()}
          onCancel={() => setConfirming(false)}
        />
      )}
    </>
  );
}
