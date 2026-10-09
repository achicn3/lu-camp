"use client";
// 品項編輯（店主 2026-10-09：每個餐飲要能重新設定內容）：品名、分類、售價、成本、介紹、選項群組
// 在同一個視窗改、一次存。選項群組勾的順序＝點餐時問的順序（docs/44 §3.2、§3.6）。
// 只送改過的欄位；成本清空＝未知（送 null，不可當 0——報表會以為毛利 100%）。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { groupRuleText, useOptionGroups } from "@/features/menu/OptionGroupsSection";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { parseNtd } from "@/lib/money";

type MenuItemRead = components["schemas"]["MenuItemRead"];
type MenuItemUpdate = components["schemas"]["MenuItemUpdateRequest"];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function sameOrder(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i]);
}

interface Draft {
  name: string;
  category: string;
  price: string;
  cost: string;
  description: string;
}

/** 草稿與原本不同的欄位；資料不合法丟錯（訊息直接給店員看）。 */
function changesOf(item: MenuItemRead, draft: Draft): MenuItemUpdate {
  const name = draft.name.trim();
  if (name === "") throw new Error("請輸入品名");
  const price = parseNtd(draft.price);
  if (price === null || price <= 0) throw new Error("售價須為正整數元");
  const costText = draft.cost.trim();
  const cost = costText === "" ? null : parseNtd(costText);
  if (costText !== "" && (cost === null || cost < 0)) throw new Error("成本須為 0 以上的整數元");
  const changes: MenuItemUpdate = {};
  if (name !== item.name) changes.name = name;
  const category = draft.category.trim() === "" ? null : draft.category.trim();
  if (category !== (item.category ?? null)) changes.category = category;
  if (price !== parseNtd(item.unit_price)) changes.unit_price = String(price);
  const costNow = item.unit_cost == null ? null : parseNtd(item.unit_cost);
  if (cost !== costNow) changes.unit_cost = cost === null ? null : String(cost);
  const description = draft.description.trim() === "" ? null : draft.description.trim();
  if (description !== (item.description ?? null)) changes.description = description;
  return changes;
}

export function MenuItemEditDialog({
  item,
  onDone,
  onCancel,
}: {
  item: MenuItemRead;
  onDone: () => void;
  onCancel: () => void;
}) {
  const groups = useOptionGroups();
  const original = item.option_groups.map((g) => g.id);
  const [picked, setPicked] = useState<number[]>(original);
  const [draft, setDraft] = useState<Draft>({
    name: item.name,
    category: item.category ?? "",
    price: item.unit_price,
    cost: item.unit_cost ?? "",
    description: item.description ?? "",
  });
  const [error, setError] = useState<string | null>(null);
  // 改了任何欄位就收掉上一次的錯誤訊息，免得改對了還掛著舊的紅字。
  const patchDraft = (change: Partial<Draft>) => {
    setError(null);
    setDraft((prev) => ({ ...prev, ...change }));
  };

  const save = useMutation({
    mutationFn: async () => {
      const changes = changesOf(item, draft);
      if (!sameOrder(picked, original)) {
        const { data, error: err } = await api.PUT("/api/v1/menu-items/{item_id}/option-groups", {
          params: { path: { item_id: item.id } },
          body: { group_ids: picked },
        });
        if (!data) throw new Error(extractDetail(err) ?? "設定選項失敗");
      }
      if (Object.keys(changes).length > 0) {
        const { data, error: err } = await api.PATCH("/api/v1/menu-items/{item_id}", {
          params: { path: { item_id: item.id } },
          body: changes,
        });
        if (!data) throw new Error(extractDetail(err) ?? "儲存失敗");
      }
    },
    onSuccess: () => {
      setError(null);
      onDone();
    },
    onError: (err: Error) => setError(err.message),
  });

  function toggle(id: number) {
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label={`編輯 ${item.name}`}>
      <div className="card menu-item-options-dialog">
        <h2>編輯 {item.name}</h2>
        <div className="menu-form-grid">
          <label className="field">
            <span className="field-label">品名</span>
            <input
              aria-label="品名"
              maxLength={150}
              value={draft.name}
              onChange={(e) => patchDraft({ name: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">分類（選填）</span>
            <input
              aria-label="分類"
              maxLength={50}
              value={draft.category}
              placeholder="例如：咖啡"
              onChange={(e) => patchDraft({ category: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">售價（整數元）</span>
            <input
              aria-label="售價"
              inputMode="numeric"
              value={draft.price}
              onChange={(e) => patchDraft({ price: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">成本（整數元，留白＝未知）</span>
            <input
              aria-label="成本"
              inputMode="numeric"
              value={draft.cost}
              onChange={(e) => patchDraft({ cost: e.target.value })}
            />
          </label>
        </div>
        <label className="field">
          <span className="field-label">介紹</span>
          <textarea
            aria-label="介紹"
            rows={3}
            maxLength={500}
            value={draft.description}
            placeholder="例如：濃縮咖啡加鮮奶，可換燕麥奶"
            onChange={(e) => patchDraft({ description: e.target.value })}
          />
        </label>
        <h3>選項群組</h3>
        <p className="hint">勾選的順序就是點餐時問的順序。</p>
        {groups.isError && (
          <p role="alert" className="form-error">
            {groups.error.message}
          </p>
        )}
        <ul className="menu-item-group-list">
          {(groups.data ?? []).map((group) => {
            const order = picked.indexOf(group.id);
            return (
              <li key={group.id}>
                <label className="menu-daily-limit">
                  <input type="checkbox" checked={order >= 0} onChange={() => toggle(group.id)} />
                  {order >= 0 && <span className="menu-item-group-order">{order + 1}</span>}
                  {group.name}
                  <span className="hint">
                    {groupRuleText(group)}・{group.options.map((o) => o.name).join("、")}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
        {groups.isSuccess && groups.data.length === 0 && (
          <p className="hint">還沒有選項群組，先到「選項群組」分頁新增。</p>
        )}
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="button" className="btn-ghost" onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={save.isPending || !groups.isSuccess}
            onClick={() => save.mutate()}
          >
            儲存
          </button>
        </div>
      </div>
    </div>
  );
}
