"use client";
// 品項的「選項與介紹」（docs/44 §3.2、§3.6；O2）：勾要掛的選項群組（勾的順序＝點餐時問的順序）、
// 填介紹（線上菜單與收據會用）。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { groupRuleText, useOptionGroups } from "@/features/menu/OptionGroupsSection";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type MenuItemRead = components["schemas"]["MenuItemRead"];

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

export function MenuItemOptionsDialog({
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
  const [description, setDescription] = useState(item.description ?? "");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      if (!sameOrder(picked, original)) {
        const { data, error: err } = await api.PUT("/api/v1/menu-items/{item_id}/option-groups", {
          params: { path: { item_id: item.id } },
          body: { group_ids: picked },
        });
        if (!data) throw new Error(extractDetail(err) ?? "設定選項失敗");
      }
      const next = description.trim() === "" ? null : description.trim();
      if (next !== (item.description ?? null)) {
        const { data, error: err } = await api.PATCH("/api/v1/menu-items/{item_id}", {
          params: { path: { item_id: item.id } },
          body: { description: next },
        });
        if (!data) throw new Error(extractDetail(err) ?? "儲存介紹失敗");
      }
    },
    onSuccess: onDone,
    onError: (err: Error) => setError(err.message),
  });

  function toggle(id: number) {
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={`${item.name} 的選項與介紹`}
    >
      <div className="card menu-item-options-dialog">
        <h2>{item.name}</h2>
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
                  <input
                    type="checkbox"
                    checked={order >= 0}
                    onChange={() => toggle(group.id)}
                  />
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
        <label className="field">
          <span className="field-label">介紹</span>
          <textarea
            rows={3}
            maxLength={500}
            value={description}
            placeholder="例如：濃縮咖啡加鮮奶，可換燕麥奶"
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
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
