"use client";
// 店員推薦（菜單 →「線上發布」分頁；店主 2026-10-10）：一份有序清單，可挑餐飲品項、手沖體驗卡、
// 帶著走商品。客人掃碼直接進完整菜單，「店員推薦」排第一並預設打開，順序就是這裡排的。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiDetail } from "@/features/menu/experienceOptions";
import { RETAIL_KEY } from "@/features/menu/RetailSection";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type Pick = components["schemas"]["StaffPickRef"];
type Choice = { key: string; label: string; ref: Pick };

const keyOf = (ref: Pick) => `${ref.kind}:${ref.id}`;

export function StaffPicksSection() {
  const picks = useQuery({
    queryKey: ["staff-picks"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/online-order/staff-picks");
      if (!data) throw new Error(apiDetail(error, "讀取店員推薦失敗"));
      return data;
    },
  });
  const items = useQuery({
    queryKey: ["menu-items"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/menu-items");
      if (!data) throw new Error(apiDetail(error, "讀取菜單失敗"));
      return data;
    },
  });
  const experiences = useQuery({
    queryKey: ["menu-experiences"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/online-order/experiences");
      if (!data) throw new Error(apiDetail(error, "讀取體驗卡失敗"));
      return data;
    },
  });
  const retail = useQuery({
    queryKey: RETAIL_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/online-order/retail");
      if (!data) throw new Error(apiDetail(error, "讀取帶著走商品失敗"));
      return data;
    },
  });
  const failed = picks.error ?? items.error ?? experiences.error ?? retail.error;
  const ready = picks.data && items.data && experiences.data && retail.data;
  const choices: Choice[] = ready
    ? [
        ...items.data!.map((item) => ({ key: `item:${item.id}`, label: item.name, ref: { kind: "item" as const, id: item.id } })),
        ...experiences.data!.map((exp) => ({
          key: `experience:${exp.id}`,
          label: `手沖體驗：${exp.title}`,
          ref: { kind: "experience" as const, id: exp.id },
        })),
        ...retail.data!.map((row) => ({
          key: `retail:${row.catalog_product_id}`,
          label: `帶著走：${row.product_name}`,
          ref: { kind: "retail" as const, id: row.catalog_product_id },
        })),
      ]
    : [];

  return (
    <section className="card menu-experiences" aria-labelledby="menu-picks-title">
      <div className="menu-experiences-head">
        <div>
          <h2 id="menu-picks-title">店員推薦</h2>
          <p className="hint">
            客人掃碼後第一個看到的分頁。可以挑餐飲、手沖體驗卡、帶著走商品，順序就是客人看到的順序。改完到上方按「發佈到線上點餐」。
          </p>
        </div>
      </div>
      {failed && <p role="alert" className="form-error">{failed.message}</p>}
      {ready && <PicksEditor initial={picks.data!.items ?? []} choices={choices} />}
    </section>
  );
}

function PicksEditor({ initial, choices }: { initial: Pick[]; choices: Choice[] }) {
  const queryClient = useQueryClient();
  // 只在第一次拿到清單時帶入；之後以畫面上排的為準。
  const [list, setList] = useState<Pick[]>(initial);
  const [notice, setNotice] = useState<string | null>(null);
  const labelOf = (ref: Pick) => choices.find((c) => c.key === keyOf(ref))?.label ?? "已移除的商品";
  const picked = new Set(list.map(keyOf));

  const save = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.PUT("/api/v1/online-order/staff-picks", { body: { items: list } });
      if (!data) throw new Error(apiDetail(error, "儲存失敗"));
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["staff-picks"], data);
      setNotice("已儲存。到上方按「發佈到線上點餐」後客人頁才會更新。");
    },
    onError: (reason: Error) => setNotice(reason.message),
  });

  const move = (index: number, delta: number) =>
    setList((current) => {
      const next = [...current];
      const [row] = next.splice(index, 1);
      next.splice(index + delta, 0, row!);
      return next;
    });

  return (
    <div className="staff-picks">
      {list.length === 0 ? (
        <p className="hint">還沒有店員推薦，客人頁不會有這個分頁。</p>
      ) : (
        <ol className="staff-pick-list" aria-label="店員推薦清單">
          {list.map((ref, index) => {
            const label = labelOf(ref);
            return (
              <li key={keyOf(ref)}>
                <span className="staff-pick-name">{label}</span>
                <span className="staff-pick-actions">
                  <button type="button" className="btn-ghost" aria-label={`上移 ${label}`} disabled={index === 0} onClick={() => move(index, -1)}>↑</button>
                  <button type="button" className="btn-ghost" aria-label={`下移 ${label}`} disabled={index === list.length - 1} onClick={() => move(index, 1)}>↓</button>
                  <button type="button" className="btn-ghost" aria-label={`移除 ${label}`} onClick={() => setList((current) => current.filter((_, i) => i !== index))}>移除</button>
                </span>
              </li>
            );
          })}
        </ol>
      )}
      <div className="staff-pick-add">
        <select
          aria-label="加入店員推薦"
          value=""
          disabled={list.length >= 30}
          onChange={(e) => {
            const choice = choices.find((c) => c.key === e.target.value);
            if (choice) setList((current) => [...current, choice.ref]);
          }}
        >
          <option value="">＋ 加一個推薦（最多 30 個）</option>
          {choices
            .filter((choice) => !picked.has(choice.key))
            .map((choice) => (
              <option key={choice.key} value={choice.key}>
                {choice.label}
              </option>
            ))}
        </select>
        <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "儲存中…" : "儲存店員推薦"}
        </button>
      </div>
      {notice !== null && <p role="status" className="hint">{notice}</p>}
    </div>
  );
}
