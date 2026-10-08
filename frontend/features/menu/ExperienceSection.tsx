"use client";
// 手沖體驗卡列表（菜單 →「線上發布」分頁；docs/63 §4、M1c）。新增與編輯各自一頁（同採購單的做法），
// 列表只放一眼看得懂的摘要。卡片引用既有品項＋預選選項，不填價格、不另管庫存。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { EFFECTS, apiDetail } from "@/features/menu/experienceOptions";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type MenuItem = components["schemas"]["MenuItemRead"];

function optionNames(item: MenuItem | undefined, ids: number[]): string[] {
  const options = item?.option_groups.flatMap((group) => group.options) ?? [];
  return ids.map((id) => options.find((option) => option.id === id)?.name ?? "已移除的選項");
}

export function ExperienceSection() {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const experiences = useQuery({
    queryKey: ["menu-experiences"],
    queryFn: async () => {
      const { data, error: e } = await api.GET("/api/v1/online-order/experiences");
      if (!data) throw new Error(apiDetail(e, "讀取體驗卡失敗"));
      return data;
    },
  });
  const items = useQuery({
    queryKey: ["menu-items"],
    queryFn: async () => {
      const { data, error: e } = await api.GET("/api/v1/menu-items");
      if (!data) throw new Error(apiDetail(e, "讀取菜單失敗"));
      return data;
    },
  });
  const remove = useMutation({
    mutationFn: async (id: number) => {
      const { error: e, response } = await api.DELETE("/api/v1/online-order/experiences/{experience_id}", {
        params: { path: { experience_id: id } },
      });
      if (!response.ok) throw new Error(apiDetail(e, "刪除失敗"));
    },
    onSuccess: () => {
      setConfirming(null);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["menu-experiences"] });
    },
    onError: (reason: Error) => setError(reason.message),
  });
  const byId = new Map((items.data ?? []).map((item) => [item.id, item]));

  return (
    <section className="card menu-experiences" aria-labelledby="menu-experiences-title">
      <div className="menu-experiences-head">
        <div>
          <h2 id="menu-experiences-title">手沖體驗卡</h2>
          <p className="hint">用現有品項加上預選的選項（例如手沖咖啡＋蜜桃蹦蹦），價格與份數都跟著原品項。改完到上方按「發佈到線上點餐」。</p>
        </div>
        <Link href="/menu/experiences/new" className="btn-primary">新增體驗卡</Link>
      </div>
      {(experiences.isError || items.isError) && (
        <p role="alert" className="form-error">{(experiences.error ?? items.error)?.message}</p>
      )}
      {error !== null && <p role="alert" className="form-error">{error}</p>}
      {experiences.data && experiences.data.length === 0 && <p className="hint">還沒有體驗卡。</p>}
      <ul className="menu-experience-list">
        {(experiences.data ?? []).map((row) => {
          const item = byId.get(row.menu_item_id);
          const ids = row.option_ids ?? [];
          return (
            <li key={row.id} className={`menu-experience-row${row.is_active ? "" : " is-off"}`}>
              <span className={`menu-experience-swatch brew-swatch-${row.theme}`} aria-hidden="true" />
              <div className="menu-experience-text">
                <b>{row.title}</b>
                <span>{item?.name ?? "已移除的品項"}{ids.length ? ` · ${optionNames(item, ids).join("、")}` : ""}</span>
                <small>{EFFECTS.find((e) => e.value === row.effect)?.label}{row.is_active ? "" : " · 停用中"}</small>
              </div>
              <div className="menu-experience-actions">
                {confirming === row.id ? (
                  <>
                    <button type="button" className="btn-danger" disabled={remove.isPending} onClick={() => remove.mutate(row.id)}>確定刪除</button>
                    <button type="button" className="btn-ghost" onClick={() => setConfirming(null)}>取消</button>
                  </>
                ) : (
                  <>
                    <Link href={`/menu/experiences/${row.id}`} className="btn-ghost">編輯</Link>
                    <button type="button" className="btn-ghost" onClick={() => setConfirming(row.id)}>刪除</button>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
