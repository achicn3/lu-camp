"use client";
// 續點折數（2026-10-16）：可續點的品項續點時照原價（含加料）的幾成算，80＝八折。
// 存在店內設定；線上點餐要重新發佈才會換成新折數。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { refillLabel } from "@/features/pos/cart";
import { api } from "@/lib/api";

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export function RefillSetting() {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.GET("/api/v1/settings")).data ?? null,
  });
  const current = settings.data?.menu_refill_pct;
  // null＝還沒改過，顯示設定裡的值。
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const value = draft ?? (current === undefined ? "" : String(current));

  const save = useMutation({
    mutationFn: async (pct: number) => {
      const { data, error: err } = await api.PATCH("/api/v1/settings", {
        body: { menu_refill_pct: pct },
      });
      if (!data) throw new Error(extractDetail(err) ?? "儲存失敗");
      return data;
    },
    onSuccess: () => {
      setDraft(null);
      setSaved(true);
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    const pct = /^\d+$/.test(value.trim()) ? Number(value) : Number.NaN;
    if (!Number.isInteger(pct) || pct < 1 || pct > 99) {
      setError("續點折數請輸入 1–99 的整數");
      return;
    }
    setError(null);
    save.mutate(pct);
  }

  const shown = current === undefined ? null : current;
  return (
    <form className="card menu-refill-setting" onSubmit={submit}>
      <label className="field">
        <span className="field-label">續點折數（%）</span>
        <input
          inputMode="numeric"
          value={value}
          onChange={(e) => {
            setDraft(e.target.value);
            setError(null);
            setSaved(false);
          }}
        />
      </label>
      <button type="submit" className="btn-secondary" disabled={save.isPending}>
        儲存續點折數
      </button>
      <p className="hint">
        {shown === null ? "" : `目前續點打 ${refillLabel(shown)}（照原價含加料的 ${shown}% 算）。`}
        在品項「編輯」勾「可續點」才能續點；改了折數要重新發佈線上菜單，客人那邊才會換。
      </p>
      {saved && (
        <p role="status" className="hint">
          已儲存。
        </p>
      )}
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </form>
  );
}
