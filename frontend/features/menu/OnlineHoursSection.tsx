"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type Hours = components["schemas"]["OnlineHours"];
const DAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"];
const EMPTY: Hours = { enabled: false, days: Array(7).fill(null) };

export function OnlineHoursSection() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Hours | null>(null);
  const [notice, setNotice] = useState("");
  const cache = useQueryClient();
  const settings = useQuery({
    queryKey: ["online-hours-settings"], enabled: open, refetchOnWindowFocus: false,
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/settings");
      if (!data) throw new Error("讀取營業時間失敗，請重試。");
      return data;
    },
  });
  const hours = draft ?? settings.data?.online_order_hours ?? EMPTY;
  const days = hours.days ?? EMPTY.days!;
  const save = useMutation({
    mutationFn: async () => {
      const { data } = await api.PATCH("/api/v1/settings", { body: { online_order_hours: hours } });
      if (!data) throw new Error("儲存失敗，請檢查時間或稍後重試。");
      return data;
    },
    onSuccess: (data) => {
      setDraft(data.online_order_hours ?? EMPTY);
      setNotice("已儲存，請按「發佈到線上點餐」讓營業時間生效。");
      void cache.invalidateQueries({ queryKey: ["online-hours-settings"] });
    },
  });
  const change = (next: Hours) => { setDraft(next); setNotice(""); save.reset(); };
  const invalid = days.some(day => day && (!day.opens || !day.closes || day.opens === day.closes));
  return <details className="online-hours" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>營業時間</summary>
    {open && <>
      <p className="hint">依台北時間接單。結束早於開始代表跨午夜；手動暫停接單仍優先生效。儲存後請再發佈。</p>
      {settings.isPending && <p role="status">讀取中…</p>}
      {settings.isError && <p role="alert">{settings.error.message}<button type="button" className="btn-ghost" onClick={() => void settings.refetch()}>重試</button></p>}
      {settings.data && <form onSubmit={event => { event.preventDefault(); if (!invalid && !save.isPending) save.mutate(); }}>
        <fieldset disabled={save.isPending}>
          <label><input type="checkbox" checked={hours.enabled ?? false} onChange={event => change({ ...hours, enabled: event.target.checked })} />依營業時間接單</label>
          {DAYS.map((name, index) => {
            const day = days[index];
            return <div className="online-hours-row" key={name}>
              <label><input type="checkbox" aria-label={`${name} 營業`} checked={day != null}
                onChange={event => change({ ...hours, days: days.map((value, i) => i === index ? event.target.checked ? { opens: "09:00", closes: "18:00" } : null : value) })} />{name}</label>
              {day ? <>
                <input type="time" required aria-label={`${name} 開始`} value={day.opens} onChange={event => change({ ...hours, days: days.map((value, i) => i === index ? { ...day, opens: event.target.value } : value) })} />
                <span>至</span>
                <input type="time" required aria-label={`${name} 結束`} value={day.closes} onChange={event => change({ ...hours, days: days.map((value, i) => i === index ? { ...day, closes: event.target.value } : value) })} />
                {day.closes < day.opens && <span className="hint">隔日</span>}
              </> : <span className="hint">公休</span>}
            </div>;
          })}
          {invalid && <p role="alert">請填寫完整時間，開始與結束不可相同。</p>}
          <button type="submit" className="btn-secondary" disabled={invalid || save.isPending}>{save.isPending ? "儲存中…" : "儲存營業時間"}</button>
        </fieldset>
      </form>}
      {notice && <p role="status">{notice}</p>}
      {save.isError && <p role="alert">{save.error.message}</p>}
    </>}
  </details>;
}
