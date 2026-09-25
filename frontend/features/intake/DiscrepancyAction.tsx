"use client";
// 上架時的差異（docs/42 §8）：少件或壞到不能賣 → 記下件數與原因，那幾件報廢出庫。
// 成交件數與成本不改（客人簽過的）；少掉的成本就是損失。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type Item = components["schemas"]["IntakeItemRead"];

const QUICK_REASONS = ["找不到", "壞了不能賣"];

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

export function DiscrepancyAction({
  batchId,
  item,
  onDone,
}: {
  batchId: number;
  item: Item;
  onDone: () => void;
}) {
  const bulk = item.kind === "BULK_LOT";
  const [open, setOpen] = useState(false);
  const [qty, setQty] = useState("1");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const report = useMutation({
    mutationFn: async () => {
      const count = bulk ? Number(qty) : 1;
      if (!Number.isInteger(count) || count < 1 || count > item.qty) {
        throw new Error(`少的件數請填 1 到 ${item.qty}`);
      }
      if (!reason.trim()) throw new Error("請寫原因（例如：找不到、壞了不能賣）");
      const { data, error: apiErr } = await api.POST(
        "/api/v1/intake-batches/{batch_id}/discrepancies",
        {
          params: { path: { batch_id: batchId } },
          body: { kind: bulk ? "BULK_LOT" : "SERIALIZED", id: item.id, qty: count, reason: reason.trim() },
        },
      );
      if (!data) throw new Error(detail(apiErr) ?? "記差異失敗");
      return data;
    },
    onSuccess: () => {
      setOpen(false);
      setError(null);
      onDone();
    },
    onError: (e: Error) => setError(e.message),
  });

  if (!open) {
    return (
      <button type="button" className="btn-ghost intake-discrepancy-btn" onClick={() => setOpen(true)}>
        少了／壞了
      </button>
    );
  }
  return (
    <div className="intake-discrepancy" role="group" aria-label={`${item.code} 記差異`}>
      {bulk ? (
        <label className="intake-inline-field">
          少了
          <input
            aria-label="少了幾件"
            inputMode="numeric"
            className="intake-custom-discount"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
          />
          ／{item.qty} 件
        </label>
      ) : (
        <span>這件不能上架</span>
      )}
      {QUICK_REASONS.map((text) => (
        <button
          key={text}
          type="button"
          className="btn-secondary"
          aria-pressed={reason === text}
          onClick={() => setReason(text)}
        >
          {text}
        </button>
      ))}
      <input
        aria-label="差異原因"
        placeholder="或自己寫原因"
        maxLength={200}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <button type="button" className="btn-primary" disabled={report.isPending} onClick={() => report.mutate()}>
        確定（{bulk ? "這幾件" : "這件"}報廢）
      </button>
      <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>
        取消
      </button>
      <span className="hint">成本與客人簽的件數不改；少掉的算損失。</span>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </div>
  );
}
