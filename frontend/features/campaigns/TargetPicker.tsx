"use client";
// 門市活動的「指定商品」範圍（docs/40 P1b）：包含或排除品牌、型號、分類、一般商品、販售籃、單件商品。
// 沒有任何「只套用在」＝上面勾的品項種類全部適用；「不套用」永遠優先。實際比對在後端定價。
import { useQuery } from "@tanstack/react-query";
import { type Dispatch, type SetStateAction, useEffect, useRef, useState } from "react";

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

const NOUN: Record<TargetType, string> = {
  BRAND: "品牌",
  PRODUCT_MODEL: "品牌",
  CATEGORY: "分類",
  CATALOG_PRODUCT: "一般商品",
  BULK_BASKET: "販售籃",
  SERIALIZED_ITEM: "商品",
};

// 型號清單一次最多列幾筆；更多的用「搜尋型號」找（Codex 審查：不能只給前 50 個）。
const MODEL_LIMIT = 50;

interface Option {
  id: number;
  label: string;
}

/** 依類型搜尋可加入的項目；型號走兩段式（先選品牌，再列／搜該品牌的型號）。 */
function useOptions(type: TargetType, q: string, brand: Option | null) {
  const pickingModel = type === "PRODUCT_MODEL" && brand !== null;
  return useQuery({
    queryKey: ["campaign-target-options", type, q, brand?.id ?? null],
    enabled: type !== "SERIALIZED_ITEM" && (q.trim() !== "" || pickingModel),
    queryFn: async (): Promise<Option[]> => {
      const term = q.trim() || undefined;
      if (pickingModel) {
        const { data } = await api.GET("/api/v1/product-models", {
          params: { query: { brand_id: brand.id, q: term, limit: MODEL_LIMIT } },
        });
        return (data ?? []).map((m) => ({ id: m.id, label: `${brand.label} ${m.name}` }));
      }
      const query = { q: term, limit: 20 };
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
      const { data } = await api.GET("/api/v1/bulk-baskets", { params: { query: { q: term } } });
      return (data ?? []).map((b) => ({ id: b.id, label: b.name }));
    },
  });
}

export function TargetPicker({
  targets,
  onChange,
  onLookupPendingChange,
  legend = "指定商品（選填）",
  includeOnly = false,
}: {
  targets: PickedTarget[];
  onChange: Dispatch<SetStateAction<PickedTarget[]>>;
  /** 條碼查詢進行中時通知表單：查詢回來前不可建立活動，否則會少了這個範圍。 */
  onLookupPendingChange: (pending: boolean) => void;
  /** 標題（組合價的每一格用「第 N 樣商品」）。 */
  legend?: string;
  /** 只有「包含」（組合價的格子）：不顯示包含／排除切換與說明。 */
  includeOnly?: boolean;
}) {
  const [mode, setMode] = useState<TargetMode>("INCLUDE");
  const [type, setType] = useState<TargetType>("BRAND");
  const [q, setQ] = useState("");
  // 型號兩段式：先選品牌、再列該品牌的型號（型號名稱常重複，單查型號會分不出是哪個牌子）。
  const [brand, setBrand] = useState<Option | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 一次只查一個條碼：兩個查詢重疊時，先回來的會把「查詢中」解除，後一個還沒回來就能送出。
  const [lookingUp, setLookingUp] = useState(false);
  // 表單建立成功後會換 key 重掛：舊的查詢晚回來時不可再把範圍塞進下一張活動。
  const mounted = useRef(true);
  // 查詢中被拿掉（組合價移除那一樣、切換活動類型）：卸載時要替它回報「查完了」，
  // 否則表單的查詢中計數永遠降不回 0，建立鈕一直鎖著（Codex 審查）。
  const pending = useRef(false);
  const reportPending = useRef(onLookupPendingChange);
  useEffect(() => {
    reportPending.current = onLookupPendingChange;
  }, [onLookupPendingChange]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (pending.current) {
        pending.current = false;
        reportPending.current(false);
      }
    };
  }, []);
  const options = useOptions(type, q, type === "PRODUCT_MODEL" ? brand : null);
  const pickingModel = type === "PRODUCT_MODEL" && brand !== null;
  const choosingBrandForModel = type === "PRODUCT_MODEL" && brand === null;

  /** 一律以「最新的清單」合併：非同步的條碼查詢回來時，不會蓋掉期間加入／移除的項目。 */
  function add(entry: PickedTarget) {
    setError(null);
    onChange((prev) =>
      prev.some(
        (t) =>
          t.mode === entry.mode &&
          t.target_type === entry.target_type &&
          t.target_id === entry.target_id,
      )
        ? prev
        : [...prev, entry],
    );
  }

  async function addByCode() {
    const code = q.trim();
    if (!code || lookingUp) return;
    // 查詢期間店員可能切換包含／排除：以按下「加入」當下的選擇為準。
    const pickedMode = mode;
    setLookingUp(true);
    pending.current = true;
    onLookupPendingChange(true);
    let data;
    try {
      ({ data } = await api.GET("/api/v1/serialized-items/by-code/{item_code}", {
        params: { path: { item_code: code } },
      }));
    } catch {
      if (mounted.current) setError("查詢商品失敗，請再按一次「加入這件」");
      return;
    } finally {
      if (mounted.current) {
        setLookingUp(false);
        pending.current = false;
        onLookupPendingChange(false);
      }
    }
    if (!mounted.current) return;
    if (!data) {
      setError(`找不到條碼 ${code} 的商品`);
      return;
    }
    add({
      mode: pickedMode,
      target_type: "SERIALIZED_ITEM",
      target_id: data.id,
      label: `${data.name}（${data.item_code}）`,
    });
    setQ((current) => (current.trim() === code ? "" : current));
  }

  function reset(nextType: TargetType) {
    setType(nextType);
    setQ("");
    setBrand(null);
    setError(null);
  }

  function chooseBrand(option: Option) {
    setBrand(option);
    setQ("");
  }

  // 已經加在目前這一邊（包含／排除）的，不再列成候選。
  const candidates = (options.data ?? []).filter(
    (o) => !targets.some((t) => t.mode === mode && t.target_type === type && t.target_id === o.id),
  );
  const searchLabel = pickingModel ? "搜尋型號" : SEARCH_LABEL[type];
  const noMatch =
    type !== "SERIALIZED_ITEM" &&
    q.trim() !== "" &&
    options.isSuccess &&
    options.data.length === 0;
  const includes = targets.filter((t) => t.mode === "INCLUDE");
  const excludes = targets.filter((t) => t.mode === "EXCLUDE");

  return (
    <fieldset className="campaign-scope-fieldset" aria-label={legend}>
      <legend>{legend}</legend>
      {!includeOnly && (
        <p className="hint">
          不指定＝上面勾的品項全部適用。可細到品牌、型號或單件，每種都可以加很多個；
          「不套用」優先於「只套用在」。
        </p>
      )}

      {!includeOnly && (
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
      )}

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

        {pickingModel && (
          <p className="campaign-target-brand">
            品牌：{brand.label}{" "}
            <button type="button" className="btn-ghost" onClick={() => reset("PRODUCT_MODEL")}>
              換品牌
            </button>
          </p>
        )}
        <label className="field">
          <span className="field-label">{searchLabel}</span>
          <input
            aria-label={searchLabel}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              // 這個選擇器在建立活動的表單裡：Enter 不可以把整張活動送出（範圍還沒選完）。
              if (e.key !== "Enter") return;
              e.preventDefault();
              if (type === "SERIALIZED_ITEM") void addByCode();
            }}
          />
        </label>
        {type === "SERIALIZED_ITEM" && (
          <button
            type="button"
            className="btn-secondary"
            disabled={lookingUp}
            onClick={() => void addByCode()}
          >
            {lookingUp ? "查詢中…" : "加入這件"}
          </button>
        )}
      </div>

      {pickingModel && (
        <p className="hint">可以接著加同品牌的其他型號；要加別的品牌的型號，按「換品牌」。</p>
      )}
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {noMatch && <p className="hint">查無符合的{pickingModel ? "型號" : NOUN[type]}</p>}

      {type !== "SERIALIZED_ITEM" && (
        <div className="campaign-target-options">
          {(choosingBrandForModel ? options.data ?? [] : candidates).map((o) =>
            choosingBrandForModel ? (
              <button
                key={o.id}
                type="button"
                className="chip"
                aria-label={`選 ${o.label}`}
                onClick={() => chooseBrand(o)}
              >
                {o.label} ›
              </button>
            ) : (
              <button
                key={o.id}
                type="button"
                className="chip"
                aria-label={`加入 ${o.label}`}
                onClick={() =>
                  add({ mode, target_type: type, target_id: o.id, label: o.label })
                }
              >
                ＋ {o.label}
              </button>
            ),
          )}
        </div>
      )}

      <TargetChips title={includeOnly ? "符合其中一樣就算" : "只套用在"} items={includes} onChange={onChange} />
      <TargetChips title="不套用" items={excludes} onChange={onChange} />
    </fieldset>
  );
}

function TargetChips({
  title,
  items,
  onChange,
}: {
  title: string;
  items: PickedTarget[];
  onChange: Dispatch<SetStateAction<PickedTarget[]>>;
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
            onClick={() =>
              onChange((prev) =>
                prev.filter(
                  (x) =>
                    !(
                      x.mode === t.mode &&
                      x.target_type === t.target_type &&
                      x.target_id === t.target_id
                    ),
                ),
              )
            }
          >
            ×
          </button>
        </span>
      ))}
    </div>
  );
}
