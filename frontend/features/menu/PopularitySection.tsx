"use client";
// 人氣標籤（菜單 →「線上發布」分頁；docs/63 §7 M2b、店主 2026-10-10）：只用 POS 真實成交算，
// 各分類前三名、淨銷量達門檻才上榜（第一名「人氣 No.1」、二三名「人氣推薦」）。
// 跟著售完同步自動更新到客人頁，不必重新發佈。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiDetail } from "@/features/menu/experienceOptions";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type Popularity = components["schemas"]["PopularityRead"];

const WINDOWS = [7, 14, 30, 60, 90] as const;
type WindowDays = (typeof WINDOWS)[number];

export function PopularitySection() {
  const current = useQuery({
    queryKey: ["menu-popularity"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/online-order/popularity");
      if (!data) throw new Error(apiDetail(error, "讀取人氣設定失敗"));
      return data;
    },
  });
  return (
    <section className="card menu-experiences" aria-labelledby="menu-popularity-title">
      <div className="menu-experiences-head">
        <div>
          <h2 id="menu-popularity-title">人氣標籤</h2>
          <p className="hint">
            只看收銀機的真實成交（扣掉作廢與退貨），每個分類前三名、達到門檻才標「人氣 No.1」「人氣推薦」。賣出後幾秒內自動更新，不必重新發佈。
          </p>
        </div>
      </div>
      {current.isError && <p role="alert" className="form-error">{current.error.message}</p>}
      {current.data && <PopularityForm current={current.data} />}
    </section>
  );
}

function PopularityForm({ current }: { current: Popularity }) {
  const queryClient = useQueryClient();
  // 只在第一次拿到設定時帶入；之後以店主在畫面上改的為準。
  const [active, setActive] = useState(current.is_active);
  const [windowDays, setWindowDays] = useState<WindowDays>(current.window_days);
  const [minQty, setMinQty] = useState(String(current.min_qty));
  const [notice, setNotice] = useState<string | null>(null);

  const min = /^\d+$/.test(minQty.trim()) ? Number(minQty) : Number.NaN;
  const minValid = Number.isInteger(min) && min >= 1 && min <= 999;

  const save = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.PUT("/api/v1/online-order/popularity", {
        body: { is_active: active, window_days: windowDays, min_qty: min },
      });
      if (!data) throw new Error(apiDetail(error, "儲存失敗"));
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["menu-popularity"], data);
      setNotice("已儲存，幾秒內客人頁就會更新。");
    },
    onError: (reason: Error) => setNotice(reason.message),
  });

  const ranking = current.ranking;
  return (
    <form
      className="popularity-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (minValid) save.mutate();
      }}
    >
      <label className="field-toggle">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        在線上菜單顯示人氣標籤
      </label>
      <div className="popularity-fields">
        <label className="field">
          <span className="field-label">計算期間</span>
          <select
            aria-label="計算期間"
            value={windowDays}
            onChange={(e) => setWindowDays(Number(e.target.value) as WindowDays)}
          >
            {WINDOWS.map((days) => (
              <option key={days} value={days}>
                近 {days} 天
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">至少賣出幾份才上榜</span>
          <input
            aria-label="至少賣出幾份才上榜"
            inputMode="numeric"
            value={minQty}
            onChange={(e) => setMinQty(e.target.value)}
          />
        </label>
      </div>
      <button type="submit" className="btn-primary" disabled={!minValid || save.isPending}>
        {save.isPending ? "儲存中…" : "儲存人氣設定"}
      </button>
      {notice !== null && <p role="status" className="hint">{notice}</p>}
      {ranking.length === 0 ? (
        <p className="hint">還沒有品項達到門檻，客人頁不會顯示人氣標籤。</p>
      ) : (
        <ul className="popularity-board" aria-label="目前的人氣榜">
          {ranking.map((row) => (
            <li key={row.item_id}>
              {row.category}：{row.name} {row.rank === 1 ? "人氣 No.1" : "人氣推薦"}（{row.qty} 份）
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}
