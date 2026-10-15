"use client";
// 估價「詳細」的商品簡稱（店主 2026-10-14）：打字時列出以前估價打過的簡稱，像收購頁的品牌欄一樣
// 點一下帶入。只有簡稱分不出是哪一款，所以清單有兩筆以上就連品牌一起列；每筆附上次估的原價、
// 預計售價、收購價。點選後那組價格留在欄位下方，填價格時對照得到。簡稱仍可自由輸入，不必選。
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

export type ShortNameSuggestion =
  components["schemas"]["IntakeShortNameSuggestion"];
type Search = (query: string) => Promise<ShortNameSuggestion[]>;

const DEBOUNCE_MS = 200;

async function searchShortNames(
  query: string,
  excludeLineId: number | undefined,
) {
  const { data, error } = await api.GET(
    "/api/v1/intake-batches/short-name-suggestions",
    {
      params: { query: { q: query, exclude_line_id: excludeLineId } },
    },
  );
  if (error) throw new Error("讀不到以前的簡稱");
  return data;
}

function money(value: string | null | undefined): string | null {
  const amount = value == null ? null : parseNtd(value);
  return amount === null ? null : `$${formatNtd(amount)}`;
}

/** 上次估的價格，依類型換說法：散裝收的是整堆、寄售沒有收購價（售價是寄售售價）。 */
export function estimateParts(
  s: ShortNameSuggestion,
): { label: string; value: string }[] {
  const parts: { label: string; value: string }[] = [];
  const reference = money(s.reference_price);
  const listed = money(s.expected_listed_price);
  const deal = money(s.deal_cost);
  if (reference) parts.push({ label: "原價", value: reference });
  if (s.acquisition_type === "CONSIGNMENT") {
    if (listed) parts.push({ label: "寄售售價", value: listed });
    return parts;
  }
  if (listed)
    parts.push({
      label: s.acquisition_type === "BULK_LOT" ? "每件售價" : "預計售價",
      value: listed,
    });
  if (deal) {
    if (s.acquisition_type === "BULK_LOT") {
      const pieces = s.bulk_piece_count ?? 1;
      parts.push({
        label: "收購價",
        value: `整堆 ${deal}${pieces > 1 ? `（${pieces} 件）` : ""}`,
      });
    } else {
      parts.push({ label: "收購價", value: deal });
    }
  }
  return parts;
}

function estimatedOn(s: ShortNameSuggestion): string {
  const [, month, day] = s.estimated_on.split("-");
  return `${Number(month)}/${Number(day)}`;
}

function EstimateLine({ suggestion }: { suggestion: ShortNameSuggestion }) {
  const parts = estimateParts(suggestion);
  if (parts.length === 0)
    return <span className="sn-prices sn-muted">沒有估價紀錄</span>;
  return (
    <span className="sn-prices">
      {parts.map((p) => (
        <span key={p.label} className="sn-price">
          <span className="sn-price-label">{p.label}</span>
          <span className="money">{p.value}</span>
        </span>
      ))}
    </span>
  );
}

/** 上架時選的型號：「上架為 Snow Peak FD 椅」——有型號就一定帶品牌（同型號名可能不同品牌）。 */
function ListedAs({ suggestion }: { suggestion: ShortNameSuggestion }) {
  if (!suggestion.product_model_name) return null;
  const brand = suggestion.brand_name ? `${suggestion.brand_name} ` : "";
  return (
    <span className="sn-listed">
      上架為 {brand}
      {suggestion.product_model_name}
    </span>
  );
}

function When({ suggestion }: { suggestion: ShortNameSuggestion }) {
  return (
    <span className="sn-when">
      {estimatedOn(suggestion)}
      {suggestion.times > 1 ? ` · 估過 ${suggestion.times} 次` : ""}
    </span>
  );
}

export function ShortNameInput({
  value,
  onChange,
  excludeLineId,
  search,
  onApply,
}: {
  value: string;
  onChange: (value: string) => void;
  /** 正在改的這一列，自己不出現在建議裡。 */
  excludeLineId?: number;
  /** 測試用；預設查後端。 */
  search?: Search;
  /** 「帶入」：把上次估的內容填進表單（店主 2026-10-15）。沒給就不顯示按鈕。 */
  onApply?: (suggestion: ShortNameSuggestion) => void;
}) {
  const [options, setOptions] = useState<ShortNameSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [picked, setPicked] = useState<ShortNameSuggestion | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const listId = useId();
  const searchRef = useRef<Search>(
    search ?? ((q) => searchShortNames(q, excludeLineId)),
  );
  useEffect(() => {
    searchRef.current = search ?? ((q) => searchShortNames(q, excludeLineId));
  });

  const query = value.trim();
  useEffect(() => {
    if (!open || query === "") return undefined;
    let alive = true;
    const timer = setTimeout(() => {
      searchRef
        .current(query)
        .then((rows) => {
          if (!alive) return;
          setOptions(rows);
          setActive(-1);
        })
        .catch(() => {
          if (alive) setOptions([]);
        });
    }, DEBOUNCE_MS);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [query, open]);

  useEffect(() => {
    function onDoc(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node))
        setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  // 只有簡稱分不出是哪一款：兩筆以上就列品牌（店主 2026-10-14）。
  // 打品牌找到的只有一筆也要寫品牌，不然看不出為什麼列出來（店主 2026-10-15）。
  const needle = query.toLowerCase();
  const showBrand = (option: ShortNameSuggestion) =>
    options.length >= 2 ||
    (option.brand_name ?? "").toLowerCase().includes(needle);
  const menuOpen = open && query !== "" && options.length > 0;
  const shownPick =
    picked !== null && picked.short_name === value ? picked : null;

  function pick(option: ShortNameSuggestion) {
    onChange(option.short_name);
    setPicked(option);
    setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!menuOpen) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (i + 1) % options.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (i <= 0 ? options.length - 1 : i - 1));
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      pick(options[active]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="combo sn-combo" ref={ref}>
      <input
        aria-label="商品簡稱"
        value={value}
        maxLength={100}
        placeholder="例如：黑色折疊椅"
        autoComplete="off"
        role="combobox"
        aria-expanded={menuOpen}
        aria-controls={listId}
        aria-autocomplete="list"
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
        onBlur={() => setOpen(false)}
      />
      {menuOpen && (
        <div
          className="combo-menu sn-menu"
          role="listbox"
          id={listId}
          aria-label="以前估過的商品"
        >
          {options.map((option, i) => (
            <button
              type="button"
              key={`${option.short_name}-${option.brand_id ?? "none"}-${option.product_model_id ?? "none"}`}
              role="option"
              aria-selected={i === active}
              className={`sn-option${i === active ? " sn-option--active" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(option)}
            >
              <span className="sn-head">
                <span className="sn-name">{option.short_name}</span>
                {showBrand(option) &&
                  !option.product_model_name &&
                  (option.brand_name ? (
                    <span className="sn-brand">{option.brand_name}</span>
                  ) : (
                    <span className="sn-brand sn-brand--none">沒填品牌</span>
                  ))}
                <When suggestion={option} />
              </span>
              <ListedAs suggestion={option} />
              <EstimateLine suggestion={option} />
            </button>
          ))}
        </div>
      )}
      {shownPick !== null && !menuOpen && (
        <div className="sn-last" aria-label="上次估價">
          <span className="sn-last-title">
            上次估價
            {shownPick.brand_name && !shownPick.product_model_name
              ? `（${shownPick.brand_name}）`
              : ""}
            <When suggestion={shownPick} />
          </span>
          <ListedAs suggestion={shownPick} />
          <EstimateLine suggestion={shownPick} />
          {onApply && (
            <button
              type="button"
              className="btn-secondary sn-apply"
              onClick={() => onApply(shownPick)}
            >
              帶入
            </button>
          )}
        </div>
      )}
    </div>
  );
}
