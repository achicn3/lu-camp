"use client";
// 門市活動的「指定商品」範圍（docs/40 P1b）：包含或排除品牌、型號、分類、一般商品、販售籃、單件商品。
// 沒有任何「只套用在」＝上面勾的品項種類全部適用；「不套用」永遠優先。實際比對在後端定價。
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type TargetMode = components["schemas"]["CampaignTargetMode"];
type TargetType = components["schemas"]["CampaignTargetType"];

export interface PickedTarget {
  mode: TargetMode;
  target_type: TargetType;
  target_id: number;
  label: string;
}

const TYPE_OPTIONS: { value: TargetType; label: string }[] = [
  { value: "BRAND", label: "品牌" },
  { value: "PRODUCT_MODEL", label: "型號" },
  { value: "CATEGORY", label: "分類" },
  { value: "CATALOG_PRODUCT", label: "一般商品" },
  { value: "BULK_BASKET", label: "散裝販售籃" },
  { value: "SERIALIZED_ITEM", label: "單件商品（掃條碼）" },
];

const SEARCH_LABEL: Record<TargetType, string> = {
  BRAND: "搜尋品牌",
  PRODUCT_MODEL: "搜尋品牌",
  CATEGORY: "搜尋分類",
  CATALOG_PRODUCT: "搜尋一般商品",
  BULK_BASKET: "搜尋販售籃",
  SERIALIZED_ITEM: "商品條碼",
};

interface Option {
  id: number;
  label: string;
}

/** 依類型搜尋可加入的項目（品牌、分類、一般商品、販售籃；型號走兩段式）。 */
function useOptions(type: TargetType, q: string, brand: Option | null) {
  return useQuery({
    queryKey: ["campaign-target-options", type, q, brand?.id ?? null],
    enabled: type !== "SERIALIZED_ITEM" && (q.trim() !== "" || brand !== null),
    queryFn: async (): Promise<Option[]> => {
      const query = { q: q.trim() || undefined, limit: 20 };
      if (type === "PRODUCT_MODEL" && brand !== null) {
        const { data } = await api.GET("/api/v1/product-models", {
          params: { query: { brand_id: brand.id, limit: 50 } },
        });
        return (data ?? []).map((m) => ({ id: m.id, label: `${brand.label} ${m.name}` }));
      }
      if (type === "BRAND" || type === "PRODUCT_MODEL") {
        const { data } = await api.GET("/api/v1/brands", { params: { query } });
        return (data ?? []).map((b) => ({ id: b.id, label: b.name }));
      }
      if (type === "CATEGORY") {
        const { data } = await api.GET("/api/v1/categories", { params: { query } });
        return (data ?? []).map((c) => ({ id: c.id, label: c.name }));
      }
      if (type === "CATALOG_PRODUCT") {
        const { data } = await api.GET("/api/v1/catalog-products", { params: { query } });
        return (data ?? []).map((p) => ({ id: p.id, label: p.name }));
      }
      const { data } = await api.GET("/api/v1/bulk-baskets", {
        params: { query: { q: q.trim() || undefined } },
      });
      return (data ?? []).map((b) => ({ id: b.id, label: b.name }));
    },
  });
}

export function TargetPicker({
  targets,
  onChange,
}: {
  targets: PickedTarget[];
  onChange: (targets: PickedTarget[]) => void;
}) {
  const [mode, setMode] = useState<TargetMode>("INCLUDE");
  const [type, setType] = useState<TargetType>("BRAND");
  const [q, setQ] = useState("");
  // 型號兩段式：先選品牌、再列該品牌的型號（型號名稱常重複，單查型號會分不出是哪個牌子）。
  const [brand, setBrand] = useState<Option | null>(null);
  const [error, setError] = useState<string | null>(null);
  const options = useOptions(type, q, type === "PRODUCT_MODEL" ? brand : null);
  const pickingModel = type === "PRODUCT_MODEL" && brand !== null;

  function add(option: Option) {
    setError(null);
    const exists = targets.some(
      (t) => t.mode === mode && t.target_type === type && t.target_id === option.id,
    );
    if (!exists) {
      onChange([...targets, { mode, target_type: type, target_id: option.id, label: option.label }]);
    }
  }

  async function addByCode() {
    const code = q.trim();
    if (!code) return;
    const { data } = await api.GET("/api/v1/serialized-items/by-code/{item_code}", {
      params: { path: { item_code: code } },
    });
    if (!data) {
      setError(`找不到條碼 ${code} 的商品`);
      return;
    }
    add({ id: data.id, label: `${data.name}（${data.item_code}）` });
    setQ("");
  }

  function reset(nextType: TargetType) {
    setType(nextType);
    setQ("");
    setBrand(null);
    setError(null);
  }

  // 已經加在目前這一邊（包含／排除）的，不再列成候選。
  const candidates = (options.data ?? []).filter(
    (o) => !targets.some((t) => t.mode === mode && t.target_type === type && t.target_id === o.id),
  );
  const includes = targets.filter((t) => t.mode === "INCLUDE");
  const excludes = targets.filter((t) => t.mode === "EXCLUDE");

  return (
    <fieldset className="campaign-scope-fieldset" aria-label="指定商品（選填）">
      <legend>指定商品（選填）</legend>
      <p className="hint">
        不指定＝上面勾的品項全部適用。可細到品牌、型號或單件；「不套用」優先於「只套用在」。
      </p>

      <div className="campaign-target-modes">
        <label className="campaign-checkbox">
          <input
            type="radio"
            name="campaign-target-mode"
            checked={mode === "INCLUDE"}
            onChange={() => setMode("INCLUDE")}
          />
          只套用在這些商品
        </label>
        <label className="campaign-checkbox">
          <input
            type="radio"
            name="campaign-target-mode"
            checked={mode === "EXCLUDE"}
            onChange={() => setMode("EXCLUDE")}
          />
          這些商品不套用
        </label>
      </div>

      <div className="campaign-target-picker">
        <label className="field">
          <span className="field-label">範圍類型</span>
          <select
            aria-label="範圍類型"
            value={type}
            onChange={(e) => reset(e.target.value as TargetType)}
          >
            {TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>

        {pickingModel ? (
          <p className="campaign-target-brand">
            品牌：{brand.label}{" "}
            <button type="button" className="btn-ghost" onClick={() => setBrand(null)}>
              換品牌
            </button>
          </p>
        ) : (
          <label className="field">
            <span className="field-label">{SEARCH_LABEL[type]}</span>
            <input
              aria-label={SEARCH_LABEL[type]}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && type === "SERIALIZED_ITEM") {
                  e.preventDefault();
                  void addByCode();
                }
              }}
            />
          </label>
        )}
        {type === "SERIALIZED_ITEM" && (
          <button type="button" className="btn-secondary" onClick={() => void addByCode()}>
            加入這件
          </button>
        )}
      </div>

      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}

      {type !== "SERIALIZED_ITEM" && (options.data?.length ?? 0) > 0 && (
        <div className="campaign-target-options">
          {(type === "PRODUCT_MODEL" && !pickingModel ? options.data ?? [] : candidates).map((o) =>
            type === "PRODUCT_MODEL" && !pickingModel ? (
              <button
                key={o.id}
                type="button"
                className="chip"
                aria-label={`選 ${o.label}`}
                onClick={() => setBrand(o)}
              >
                {o.label} ›
              </button>
            ) : (
              <button
                key={o.id}
                type="button"
                className="chip"
                aria-label={`加入 ${o.label}`}
                onClick={() => add(o)}
              >
                ＋ {o.label}
              </button>
            ),
          )}
        </div>
      )}

      <TargetChips title="只套用在" items={includes} targets={targets} onChange={onChange} />
      <TargetChips title="不套用" items={excludes} targets={targets} onChange={onChange} />
    </fieldset>
  );
}

function TargetChips({
  title,
  items,
  targets,
  onChange,
}: {
  title: string;
  items: PickedTarget[];
  targets: PickedTarget[];
  onChange: (targets: PickedTarget[]) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="campaign-target-chips">
      <span className="field-label">{title}：</span>
      {items.map((t) => (
        <span key={`${t.mode}-${t.target_type}-${t.target_id}`} className="chip chip-active">
          {t.label}
          <button
            type="button"
            className="chip-remove"
            aria-label={`移除 ${t.label}`}
            onClick={() => onChange(targets.filter((x) => x !== t))}
          >
            ×
          </button>
        </span>
      ))}
    </div>
  );
}
