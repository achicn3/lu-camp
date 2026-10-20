"use client";
// 線上 LINE Pay 開關（店主 2026-10-20）：管理者打開才開放客人在手機上用 LINE Pay；預設關。
// 狀態以雲端為準（拉單時順便更新）；關掉只擋新的付款，已經在付的照常完成。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ONLINE_ORDERS_KEY } from "@/features/online-orders/OnlineOrdersPanel";
import { api } from "@/lib/api";

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export function OnlineLinePaySwitch() {
  const cache = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const status = useQuery({
    queryKey: ONLINE_ORDERS_KEY,
    queryFn: async () => {
      const { data, error: err } = await api.GET("/api/v1/online-orders");
      if (!data) throw new Error(extractDetail(err) ?? "讀取線上點餐狀態失敗");
      return data;
    },
  });
  const toggle = useMutation({
    mutationFn: async (enabled: boolean) => {
      const { data, error: err } = await api.PUT("/api/v1/online-orders/linepay", {
        body: { enabled },
      });
      if (!data) throw new Error(extractDetail(err) ?? "切換失敗，請稍後再試");
      return data;
    },
    onSuccess: (data) => {
      setError(null);
      cache.setQueryData(ONLINE_ORDERS_KEY, data);
    },
    onError: (err: Error) => setError(err.message),
  });
  const enabled = status.data?.linepay_enabled ?? null;
  const configured = status.data?.linepay_configured ?? null;
  const hint =
    enabled === null
      ? "還沒跟雲端連上，連上後才能切換。"
      : configured === false
        ? "雲端還沒設定 LINE Pay 金鑰，打開也不會生效。"
        : enabled
          ? "客人可以在手機上用 LINE Pay 付款。"
          : "目前關閉：客人只能到櫃檯結帳。";
  return (
    <div className="online-linepay-switch">
      <label className="menu-refill-any">
        <input
          type="checkbox"
          checked={enabled === true}
          disabled={enabled === null || toggle.isPending}
          onChange={(e) => toggle.mutate(e.target.checked)}
        />
        <span>線上點餐開放 LINE Pay</span>
      </label>
      <p className="hint">{hint}</p>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </div>
  );
}
