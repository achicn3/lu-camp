"use client";
// 待整理時客人不賣了、拿回去（店主 2026-10-09）：走收購「選品作廢」——付的錢收回、那件退場。
// 不是「少了／壞了」報廢：報廢會把客人拿回去的東西算成店裡的損失。只限買斷的二手商品、限管理者。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Item = components["schemas"]["IntakeItemRead"];
type Result = components["schemas"]["IntakeReturnResult"];

const DEFAULT_REASON = "客人不賣了";

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

/** 退回後要告訴店員的事：收回多少現金、或購物金扣回多少。 */
function doneMessage(item: Item, result: Result): string {
  const cash = parseNtd(result.reversed_cash) ?? 0;
  const credit = parseNtd(result.reversed_credit) ?? 0;
  const parts: string[] = [];
  if (cash > 0) parts.push(`請向客人收回現金 $${formatNtd(cash)}，放進抽屜`);
  if (credit > 0) parts.push(`已從客人的購物金扣回 $${formatNtd(credit)}`);
  return `「${item.name}」已退回客人：${parts.length > 0 ? parts.join("；") : "沒有要收回的款項"}。`;
}

export function ReturnToCustomerAction({
  batchId,
  item,
  onDone,
}: {
  batchId: number;
  item: Item;
  onDone: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState(DEFAULT_REASON);
  const [error, setError] = useState<string | null>(null);

  const submit = useMutation({
    mutationFn: async () => {
      if (!reason.trim()) throw new Error("請寫原因");
      const { data, error: apiErr } = await api.POST(
        "/api/v1/intake-batches/{batch_id}/return-to-customer",
        {
          params: { path: { batch_id: batchId } },
          body: { kind: "SERIALIZED", id: item.id, reason: reason.trim() },
        },
      );
      if (!data) throw new Error(detail(apiErr) ?? "退回失敗");
      return data;
    },
    onSuccess: (result) => {
      setOpen(false);
      setError(null);
      onDone(doneMessage(item, result));
    },
    onError: (e: Error) => setError(e.message),
  });

  if (!open) {
    return (
      <button type="button" className="btn-ghost intake-discrepancy-btn" onClick={() => setOpen(true)}>
        客人不賣了（退回）
      </button>
    );
  }
  return (
    <div className="intake-discrepancy" role="group" aria-label={`${item.code} 退回客人`}>
      <span>這件還給客人，付的錢收回</span>
      <input
        aria-label="退回原因"
        maxLength={200}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <button type="button" className="btn-primary" disabled={submit.isPending} onClick={() => submit.mutate()}>
        確定退回
      </button>
      <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>
        取消
      </button>
      <span className="hint">付現的要開帳中；收購紀錄會留下這筆作廢。</span>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </div>
  );
}
