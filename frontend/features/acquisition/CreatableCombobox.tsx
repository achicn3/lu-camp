"use client";
// F6 通用「查無即建」combobox（品牌/分類/型號共用）：debounce 即查、既有選項＋「建立『輸入』」、
// 點外關閉、Enter 選唯一/建立、Escape 關閉。型別由呼叫端以生成型別約束（傳入 search/create 閉包）。
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

export interface ComboOption {
  id: number;
  name: string;
}

export function CreatableCombobox({
  label,
  onChange,
  search,
  create,
  placeholder,
  disabled = false,
}: {
  label: string;
  onChange: (option: ComboOption | null) => void;
  search: (query: string) => Promise<ComboOption[]>;
  create: (name: string) => Promise<ComboOption>;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [text, setText] = useState("");
  // 已選項目單獨記錄：選定後改以「標籤」呈現，與「還在打字」外觀明確區隔。
  // 舊版只把名稱填回輸入框，導致「手打了字但沒選」與「已選定」畫面完全相同，
  // 且改字會靜默清掉已選 → 送出才發現品牌是空的。
  const [selected, setSelected] = useState<ComboOption | null>(null);
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<ComboOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const fieldId = useId();
  // search/create 由呼叫端內聯定義（每 render 新身分）；存 ref 讓 debounce effect 不因身分變動重跑。
  const searchRef = useRef(search);
  const createRef = useRef(create);
  useEffect(() => {
    searchRef.current = search;
    createRef.current = create;
  });

  useEffect(() => {
    if (!open) return undefined;
    let active = true;
    const timer = setTimeout(() => {
      setLoading(true);
      searchRef.current(text.trim())
        .then((results) => {
          if (active) setOptions(results);
        })
        .catch(() => {
          if (active) setOptions([]);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [text, open]);

  useEffect(() => {
    function onDoc(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const trimmed = text.trim();
  const exact = options.some((o) => o.name.trim().toLowerCase() === trimmed.toLowerCase());
  const canCreate = trimmed.length > 0 && !exact && !creating;

  function pick(option: ComboOption) {
    setText(option.name);
    setSelected(option);
    setOpen(false);
    setError(null);
    onChange(option);
  }

  /** 清除已選、回到可輸入狀態（標籤上的 ✕）。 */
  function clearSelection() {
    setSelected(null);
    setText("");
    setError(null);
    onChange(null);
    setOpen(true);
  }

  async function doCreate() {
    if (!canCreate) return;
    setCreating(true);
    setError(null);
    try {
      pick(await createRef.current(trimmed));
    } catch (err) {
      setError(err instanceof Error ? err.message : "建立失敗");
    } finally {
      setCreating(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      if (options.length === 1) pick(options[0]);
      else if (canCreate) void doCreate();
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="combo" ref={ref}>
      <label className="field-label" htmlFor={fieldId}>
        {label}
      </label>
      {selected !== null ? (
        <div className="combo-chip" data-testid="combo-selected">
          <span className="combo-chip-check" aria-hidden>
            ✓
          </span>
          <span className="combo-chip-name">{selected.name}</span>
          <button
            type="button"
            className="combo-chip-clear"
            aria-label={`清除已選的${label}`}
            disabled={disabled}
            onClick={clearSelection}
          >
            ✕
          </button>
        </div>
      ) : (
        <input
          id={fieldId}
          className={`combo-input${text.trim() ? " combo-input--unconfirmed" : ""}`}
          value={text}
          placeholder={placeholder}
          disabled={disabled}
          autoComplete="off"
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            setText(event.target.value);
            setOpen(true);
            onChange(null); // 編輯即清除已選，需重新挑/建
          }}
          onKeyDown={onKeyDown}
        />
      )}
      {/* 有字卻未選定＝尚未生效，明確提示，避免店員誤以為已填好（送出才發現是空的）。 */}
      {selected === null && text.trim() !== "" && !open && (
        <p className="combo-unconfirmed-hint">尚未選定：請從清單點選，或按「建立」新增</p>
      )}
      {open && !disabled && (
        <div className="combo-menu" role="listbox">
          {loading && <div className="combo-hint">查詢中…</div>}
          {!loading &&
            options.map((option) => (
              <button
                type="button"
                key={option.id}
                role="option"
                aria-selected={false}
                className="combo-option"
                onClick={() => pick(option)}
              >
                {option.name}
              </button>
            ))}
          {canCreate && (
            <button
              type="button"
              className="combo-create"
              onClick={() => void doCreate()}
              disabled={creating}
            >
              ＋ 建立「{trimmed}」
            </button>
          )}
          {!loading && !canCreate && options.length === 0 && (
            <div className="combo-hint">{trimmed ? "查無，請繼續輸入" : "輸入以搜尋"}</div>
          )}
          {error !== null && <div className="combo-hint form-error">{error}</div>}
        </div>
      )}
    </div>
  );
}
