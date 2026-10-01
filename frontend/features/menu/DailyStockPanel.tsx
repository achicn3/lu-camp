"use client";
// 今日餐點數量（docs/44 §3.7）：每日限量的品項／選項，每天開店歸零，在這裡填當天份數。
//
// 兩種改法，對應兩種現場情境：
//   - 「改成」：開店填份數、或盤點後直接校正。會附上**畫面上看到的數字**——這段時間若有人
//     結帳賣掉一份，後端會拒絕（不能把剛賣掉的那份覆寫回來），這裡就重新讀取讓店員再確認。
//   - 「+1／−1」：剛做好一盤、或報廢一份。加減是原子的，與結帳同時進行也不會算錯。
//     減少一定要選原因（報廢／盤點校正）——報廢統計靠這個，按錯也看得出來。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

export type DailyStockEntry = components["schemas"]["DailyStockEntryRead"];
type AdjustReason = components["schemas"]["MenuStockAdjustReason"];

export const DAILY_STOCK_QUERY_KEY = ["menu-daily-stock"] as const;

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export function useDailyStock(enabled = true) {
  return useQuery({
    queryKey: DAILY_STOCK_QUERY_KEY,
    enabled,
    queryFn: async (): Promise<DailyStockEntry[]> => {
      const { data, error } = await api.GET("/api/v1/menu-daily-stock");
      if (!data) throw new Error(extractDetail(error) ?? "讀取今日餐點數量失敗");
      return data;
    },
  });
}

function parseQty(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d{1,4}$/.test(trimmed)) return null;
  return Number(trimmed);
}

function StockRow({
  entry,
  busy,
  onSet,
  onAdjust,
}: {
  entry: DailyStockEntry;
  busy: boolean;
  onSet: (qty: number) => void;
  onAdjust: (delta: number, reason: AdjustReason) => void;
}) {
  // 今天還沒填就留空讓店員輸入；填過則帶目前份數，方便直接改。
  const [draft, setDraft] = useState(entry.set_today ? String(entry.remaining) : "");
  // 按 −1 先問原因，選了才送出。
  const [askingReason, setAskingReason] = useState(false);
  const qty = parseQty(draft);
  return (
    <li className="daily-stock-row">
      <div className="daily-stock-name">
        <p className="opening-item-label">{entry.label}</p>
        <p className="hint">
          {entry.set_today
            ? entry.remaining === 0
              ? "今天已售完"
              : `今天剩 ${entry.remaining} 份`
            : "今天還沒填"}
        </p>
      </div>
      <div className="daily-stock-actions">
        <input
          className="daily-stock-input"
          inputMode="numeric"
          aria-label={`${entry.label} 今日份數`}
          placeholder="份數"
          value={draft}
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button
          type="button"
          className="btn-primary"
          disabled={busy || qty === null}
          onClick={() => qty !== null && onSet(qty)}
        >
          {entry.set_today ? "改成" : "設定"}
        </button>
        <button
          type="button"
          className="btn-ghost"
          aria-label={`${entry.label} 加一份`}
          disabled={busy}
          onClick={() => onAdjust(1, "RESTOCK")}
        >
          +1
        </button>
        {askingReason ? (
          <span className="daily-stock-reason" role="group" aria-label={`${entry.label} 減少原因`}>
            <button
              type="button"
              className="btn-ghost"
              disabled={busy}
              onClick={() => {
                setAskingReason(false);
                onAdjust(-1, "WASTE");
              }}
            >
              報廢
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={busy}
              onClick={() => {
                setAskingReason(false);
                onAdjust(-1, "CORRECTION");
              }}
            >
              盤點校正
            </button>
            <button type="button" className="btn-ghost" onClick={() => setAskingReason(false)}>
              取消
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="btn-ghost"
            aria-label={`${entry.label} 減一份`}
            disabled={busy || entry.remaining === 0}
            onClick={() => setAskingReason(true)}
          >
            −1
          </button>
        )}
      </div>
    </li>
  );
}

export function DailyStockPanel({ entries }: { entries: DailyStockEntry[] }) {
  const queryClient = useQueryClient();

  function refresh() {
    // 份數會影響：這張清單、開店檢查是否完成、POS 菜單磚的「剩 N 份／售完」。
    void queryClient.invalidateQueries({ queryKey: DAILY_STOCK_QUERY_KEY });
    void queryClient.invalidateQueries({ queryKey: ["opening-check"] });
    void queryClient.invalidateQueries({ queryKey: ["menu-items"] });
  }

  const setStock = useMutation({
    mutationFn: async ({ entry, qty }: { entry: DailyStockEntry; qty: number }) => {
      const { data, error } = await api.POST(
        "/api/v1/menu-daily-stock/{kind}/{target_id}/set",
        {
          params: { path: { kind: entry.kind, target_id: entry.id } },
          body: { qty, expected_remaining: entry.remaining },
        },
      );
      if (!data) throw new Error(extractDetail(error) ?? "設定份數失敗");
      return data;
    },
    // 成功或失敗都重讀：失敗多半是「剛剛有人結帳、數字變了」，要讓店員看到新的數字再決定。
    onSettled: refresh,
  });

  const adjust = useMutation({
    mutationFn: async ({
      entry,
      delta,
      reason,
    }: {
      entry: DailyStockEntry;
      delta: number;
      reason: AdjustReason;
    }) => {
      const { data, error } = await api.POST(
        "/api/v1/menu-daily-stock/{kind}/{target_id}/adjust",
        {
          params: { path: { kind: entry.kind, target_id: entry.id } },
          body: { delta, reason },
        },
      );
      if (!data) throw new Error(extractDetail(error) ?? "調整份數失敗");
      return data;
    },
    onSettled: refresh,
  });

  const busy = setStock.isPending || adjust.isPending;
  const error = setStock.error?.message ?? adjust.error?.message ?? null;

  return (
    <div className="daily-stock">
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <ul className="daily-stock-list">
        {entries.map((entry) => (
          // key 帶上份數與是否填過：重新讀取後輸入框要換成最新數字，不能留著舊的草稿。
          <StockRow
            key={`${entry.kind}-${entry.id}-${entry.remaining}-${entry.set_today}`}
            entry={entry}
            busy={busy}
            onSet={(qty) => setStock.mutate({ entry, qty })}
            onAdjust={(delta, reason) => adjust.mutate({ entry, delta, reason })}
          />
        ))}
      </ul>
    </div>
  );
}
