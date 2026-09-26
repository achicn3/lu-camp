"use client";
// /acquisition 收購鑑價入庫（docs/10 §/acquisition）：賣方查找/建檔 → 買斷/寄售/散裝 → 鑑價列
// （品牌/型號/分類 combobox + 雙重約束定價輔助）→ 撥款（現金/購物金/混合）→ 送出。
// 全中文（labels 單一真實來源）；金額整數元、走 OpenAPI 生成型別 client；標籤列印待後端（不放假按鈕）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { CreatableCombobox, type ComboOption } from "@/features/acquisition/CreatableCombobox";
import { ACQ_TYPE_LABEL, GRADE_LABEL, PAYOUT_LABEL, SERIALIZED_GRADES } from "@/features/acquisition/labels";
import { gradeShortName, labelConditionForGrade } from "@/features/inventory/grades";
import {
  creditPremiumPreview,
  acquisitionFromListedPrice,
  discountedPrice,
  NEAR_NEW_DISCOUNT_PCT,
  discountPercent,
  gradeFromDiscount,
  marginPct,
  netOfTaxInclusive,
  maxAcquisitionCost,
  roundUpToListedStep,
  suggestedListedPrice,
  taxAndFeeInclusivePrice,
} from "@/features/acquisition/pricing";
import { PriceHint } from "@/features/acquisition/PriceHint";
import { SellerSection } from "@/features/acquisition/SellerSection";
import { expandByQty, qtyErrors, rowsPayableTotal } from "@/features/acquisition/quantity";
import {
  type AcqType,
  type AcquisitionDraft,
  type ItemDraft,
  type LotDraft,
  validateCombined,
  validateDraft,
} from "@/features/acquisition/validation";
import { canVoid } from "@/features/acquisition/void";
import { NOTE_MAX_LENGTH } from "@/features/inventory/inventory";
import { InfoTip } from "@/features/shared/InfoTip";
import { terminalInstallationId } from "@/features/customer-display/PosCustomerDisplay";
import { VoidConfirmDialog } from "@/features/acquisition/VoidConfirmDialog";
import { openCashDrawer, printAcquisitionReceipt, printLabel } from "@/lib/agent";
import { fetchSignaturePngBase64 } from "@/lib/signature";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import { formatNtd, parseNtd } from "@/lib/money";
import {
  canDiscardIdempotencyKey,
  clearPendingAcqIdemKey,
  loadPendingAcqIdemKey,
  pendingAcqIdemKeyServerSnapshot,
  pendingAcqIdemKeySnapshot,
  savePendingAcqIdemKey,
  subscribePendingAcqIdemKey,
} from "@/lib/idempotency";
import { newIdempotencyKey } from "@/lib/uuid";

type Contact = components["schemas"]["ContactRead"];
type Category = components["schemas"]["CategoryRead"];
type BulkBasket = components["schemas"]["BulkBasketRead"];
type PricingRule = components["schemas"]["PricingRuleRead"];
type Grade = components["schemas"]["Grade"];
type PayoutMethod = components["schemas"]["PayoutMethod"];
type AcquisitionType = components["schemas"]["AcquisitionType"];

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

/** 金額字串加總；全部沒有值就回 null（例：沒撥購物金）。 */
function sumNtd(values: (string | null | undefined)[]): string | null {
  const present = values.filter((v): v is string => v != null);
  if (present.length === 0) return null;
  return String(present.reduce((sum, v) => sum + (parseNtd(v) ?? 0), 0));
}

function emptyItem(commissionPct = ""): Row {
  return {
    // 穩定的列識別：用 index 當 React key 時，刪除中間列會讓後面的列沿用同一個元件實例，
    // 連帶把前一列的內部狀態（如已選標籤）帶過去。不進 API payload（逐欄挑選）。
    rowKey: newIdempotencyKey(),
    name: "",
    grade: "",
    categoryId: null,
    brandId: null,
    productModelId: null,
    listedPrice: "",
    retailPrice: "",
    acquisitionCost: "",
    commissionPct,
    estimatedResale: "",
    discount: "",
    costManual: false,
    // 商品備註（選填）：一列一則，套用該列全部件數。
    note: "",
    // 同款多件（客人一次帶三頂一樣的帳篷）：畫面上一列，送出時展開成三筆獨立商品。
    qty: "1",
  };
}

function emptyLot(): LotDraft {
  return {
    name: "",
    categoryId: null,
    brandId: null,
    acquisitionCost: "",
    acquisitionBasis: "",
    totalQty: "",
    unitPrice: "",
    retailPrice: "",
    label: "",
    note: "",
    basketMode: "NONE",
    basketId: null,
  };
}

type Row = ItemDraft & {
  estimatedResale: string;
  rowKey: string;
  qty: string;
  discount: string;
  costManual: boolean;
  /** 已填好、收合成一行摘要（新增下一列時自動收合，點摘要可展開再改）。 */
  collapsed?: boolean;
};


// ── 品名輸入（純提示 autocomplete）──
// 只把本店用過的品名叫回來當建議，**不限制**店員輸入新品名：同型號商品常因成色/配件而
// 需要不同描述，強制選單反而綁手綁腳。用原生 datalist：行動裝置與鍵盤操作都天然可用。
function ItemNameField({
  value,
  onChange,
}: {
  value: string;
  onChange: (name: string) => void;
}) {
  const listId = useId();
  const [suggestions, setSuggestions] = useState<string[]>([]);

  useEffect(() => {
    const term = value.trim();
    let active = true;
    // 一律延後到 timer 內才動 state：effect 內同步 setState 會觸發連鎖 render（lint 規則）。
    const timer = setTimeout(() => {
      if (!term) {
        if (active) setSuggestions([]);
        return;
      }
      void api
        .GET("/api/v1/item-name-suggestions", { params: { query: { q: term, limit: 10 } } })
        .then(({ data }) => {
          if (active) setSuggestions(data ?? []);
        })
        .catch(() => {
          if (active) setSuggestions([]); // 提示失敗不擋收購，靜默降級為純輸入框
        });
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [value]);

  return (
    <label className="field">
      <span className="field-label">品名</span>
      <input
        aria-label="品名"
        list={listId}
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id={listId}>
        {suggestions.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
    </label>
  );
}

// ── 鑑價列（買斷/寄售）──
// ── 已填好的列：收合成一行摘要（一次收多件時不必一路往下捲）──
function CollapsedRow({
  index,
  row,
  type,
  onExpand,
}: {
  index: number;
  row: Row;
  type: AcqType;
  onExpand: () => void;
}) {
  const qty = type === "BUYOUT" ? Math.max(1, parseNtd(row.qty) ?? 1) : 1;
  const cost = parseNtd(row.acquisitionCost);
  const listed = parseNtd(row.listedPrice);
  const parts = [
    row.name,
    row.grade ? GRADE_LABEL[row.grade] : "未選成色",
    type === "BUYOUT" && cost !== null ? `收 ${formatNtd(cost)}${qty > 1 ? ` × ${qty}` : ""}` : null,
    listed !== null ? `售 ${formatNtd(listed)}` : null,
  ].filter((part): part is string => part !== null && part !== "");
  return (
    <button type="button" className="card acq-row-collapsed" onClick={onExpand}>
      <span className="acq-row-collapsed-no">編輯第 {index + 1} 列</span>
      <span>{parts.join("・")}</span>
    </button>
  );
}

function ItemRowCard({
  type,
  index,
  row,
  categories,
  onChange,
  onRemove,
  refreshCategories,
  defaultCommissionPct,
  defaultMarginPct,
  taxRate,
  feeRate,
  taxRateLoading,
  taxRateUnavailable,
}: {
  type: AcqType;
  index: number;
  row: Row;
  categories: Category[];
  onChange: (patch: Partial<Row>) => void;
  onRemove: () => void;
  refreshCategories: () => void;
  /** 寄售設定預設值；列尚未自訂時顯示，第一次輸入會取代而不是接在預設值後。 */
  defaultCommissionPct: string;
  defaultMarginPct: number | null;
  /** 營業稅率（settings，不寫死）；尚未載入為 null，此時不做含稅換算。 */
  taxRate: number | null;
  /** 行動支付手續費率，取兩種支付的較高者；讀不到為 0（不補、不墊高客人價格）。 */
  feeRate: number;
  /** settings 尚在載入；提示店員先不要把未稅價直接當成含稅價輸入。 */
  taxRateLoading: boolean;
  /** 設定**已回來**但拿不到可用稅率——只有這時才該對店員喊錯，載入中不算。 */
  taxRateUnavailable: boolean;
}) {
  const category = categories.find((c) => c.id === row.categoryId) ?? null;
  const targetMargin = defaultMarginPct;
  const [customDiscount, setCustomDiscount] = useState(() => row.discount !== "" && !/^[1-9]$/.test(row.discount));

  const rulesQuery = useQuery({
    queryKey: ["pricing-rules", row.categoryId],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/categories/{category_id}/pricing-rules", {
        params: { path: { category_id: row.categoryId as number } },
      });
      return data ?? [];
    },
    enabled: row.categoryId !== null,
  });

  const rule: PricingRule | null = useMemo(() => {
    if (!row.grade) return null;
    return (rulesQuery.data ?? []).find((r) => r.condition_band === row.grade) ?? null;
  }, [rulesQuery.data, row.grade]);

  const resale = parseNtd(row.estimatedResale);
  const maxCost =
    rule !== null && resale !== null
      ? maxAcquisitionCost(resale, {
          discountCeilingPct: rule.discount_ceiling_pct,
          minMarginPct: rule.min_margin_pct,
          minPriceMultiple: Number(rule.min_price_multiple),
        })
      : null;
  const cost = parseNtd(row.acquisitionCost);
  const overCost = type === "BUYOUT" && maxCost !== null && cost !== null && cost > maxCost;
  // 件數：只在買斷用（寄售一件件談抽成）。合計＝每件收購價 × 件數，即時顯示，
  // 讓店員在按送出前就看到這一列要付多少。
  const qtyIssues = type === "BUYOUT" ? qtyErrors(index, row.qty) : [];
  const multiUnitTotal =
    qtyIssues.length === 0 && cost !== null ? rowsPayableTotal([row]) : null;
  // 本列件數（僅買斷適用）：用來提示「這則備註會套用到全部 N 件」。
  const multiUnitCount =
    type === "BUYOUT" && qtyIssues.length === 0 ? (parseNtd(row.qty) ?? null) : null;
  const listed = parseNtd(row.listedPrice);
  const netProceeds = listed !== null && taxRate !== null
    ? acquisitionFromListedPrice(listed, 0, taxRate, feeRate) : null;
  const netSale = netProceeds !== null && listed !== null && taxRate !== null
    ? netOfTaxInclusive(listed, taxRate) : null;
  const margin =
    listed !== null && cost !== null && taxRate !== null
      ? marginPct(listed, cost, taxRate, feeRate)
      : null;
  // 估計轉售價（未稅）對應的客人實付價（含稅＋行動支付手續費）：自動帶入上架售價、
  // 也是按鈕上顯示的數字。
  // 一律進位到 10 的倍數（裁示 2026-09-19）：**系統填進上架售價的數字**都要 0 結尾。
  // 只進位建議按鈕、不進位這條自動同步的話，店員最常走的「打估計轉售價→自動帶入」
  // 仍然會產生 21、37 這種價格，等於這條規則沒生效。
  const resaleTaxInclusive =
    resale !== null && taxRate !== null
      ? roundUpToListedStep(taxAndFeeInclusivePrice(resale, taxRate, feeRate))
      : null;

  // onChange 由父層以行內箭頭函式傳入、每次 render 都換身分，不能進相依陣列
  // （會變成每次 render 都覆蓋一次上架售價）。改以 ref 取最新的一份；
  // 寫入放在 effect 裡，render 階段寫 ref 是 React 不允許的。
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });
  useEffect(() => {
    if (type !== "BUYOUT" || row.costManual || taxRate === null || targetMargin === null) return;
    const price = parseNtd(row.listedPrice);
    const next = price === null ? null : acquisitionFromListedPrice(price, targetMargin, taxRate, feeRate);
    const value = next === null ? "" : String(next);
    if (value !== row.acquisitionCost) onChangeRef.current({ acquisitionCost: value });
  }, [type, row.costManual, row.listedPrice, row.acquisitionCost, taxRate, feeRate, targetMargin]);

  function applyDiscount(reference: string, discount: string) {
    const amount = parseNtd(reference);
    const listed = amount === null ? null : roundUpToListedStep(discountedPrice(amount, discount));
    const grade = gradeFromDiscount(discount);
    onChange({ retailPrice: reference, discount, estimatedResale: "", costManual: false,
      listedPrice: listed === null ? "" : String(listed),
      ...(grade === null ? {} : { grade }),
    });
  }
  // 上一次同步時的估計轉售價，與我們自己填進去的那個值。
  // 用來分辨「店員動了估計轉售價」與「只是稅率設定晚到」——前者要覆蓋，後者不可以。
  // **初始值用當下的 resale/taxRate，不是 null**：切到散裝分頁時整個 ItemRowCard 會 unmount，
  // 切回來 remount 若把 ref 歸零，就會被當成「店員剛動了估計轉售價」而覆蓋掉他手打的價格
  // （實測：手打 1800 → 切散裝再切回 → 變 1050，少收 750 元）。
  // 寄售目前不做自動加稅（ADR-016 Follow-up 2）：標籤、說明與快捷鍵都要跟著誠實，
  // 不能對寄售流程說「會自動加稅帶進來」。
  const autoTaxApplies = type !== "CONSIGNMENT";
  const syncedResale = useRef<number | null>(resale);
  const syncedTaxRate = useRef<number | null>(taxRate);
  const syncedType = useRef<AcqType>(type);
  const syncedFeeRate = useRef<number>(feeRate);
  const autoFilled = useRef<string | null>(null);
  useEffect(() => {
    const resaleChanged = syncedResale.current !== resale;
    // 手續費率與稅率同屬「設定變動」：兩者都會改變同一個計算結果，補算條件必須一致。
    // 只追蹤稅率的話，設定頁改了費率、這頁重新抓到新值時，價格會停在舊費率算出來的數字。
    const rateChanged = syncedTaxRate.current !== taxRate || syncedFeeRate.current !== feeRate;
    const typeChanged = syncedType.current !== type;
    syncedResale.current = resale;
    syncedTaxRate.current = taxRate;
    syncedFeeRate.current = feeRate;
    syncedType.current = type;
    // 寄售的分潤基準另案處理（見 ADR-016 Follow-up 2），這裡先只對買斷自動加稅。
    if (type === "CONSIGNMENT") return;
    if (resale === null || taxRate === null) return;
    // 與 resaleTaxInclusive 走同一條進位，否則「認領」比對會永遠不相等而反覆覆寫。
    const target = roundUpToListedStep(taxAndFeeInclusivePrice(resale, taxRate, feeRate));
    if (target === null) return;
    const next = String(target);
    if (next === row.listedPrice) {
      autoFilled.current = next; // 值已相同：認領它，避免之後誤判成「店員手打的」
      return;
    }
    // 只有兩種情況會寫入：
    // 1. **店員在買斷分頁親自改了估計轉售價** → 一律覆蓋（店主裁示 2026-08-22）。
    // 2. 稅率／手續費率設定剛到位、或從寄售分頁切回買斷 → 僅在上架售價還空著、或仍是我們上次
    //    自動填的值時才補；否則會把店員已經手打好的價格無聲換掉。
    //
    // 為什麼「切分頁」不能走第 1 條：切分頁是導覽動作，不是定價決定。店員在寄售分頁
    // 手打了與寄售人談定的架上價 2000，只是點一下「買斷」看看，回來就變成 2100
    // ——客人多付 100、應付寄售人多 50，而且全程沒有任何提示（第三輪 M-1 實機重現）。
    // **店員自己編輯上架售價（含清空重打）也不在此列**——清空就自動填回去的話，
    // 他連重打的機會都沒有。
    const stillOurs = row.listedPrice === "" || row.listedPrice === autoFilled.current;
    const directResaleEdit = resaleChanged && !typeChanged;
    if (!(directResaleEdit || ((rateChanged || typeChanged) && stillOurs))) return;
    autoFilled.current = next;
    onChangeRef.current({ listedPrice: next });
  }, [resale, taxRate, feeRate, type, row.listedPrice]);

  function searchBrands(q: string): Promise<ComboOption[]> {
    return api
      .GET("/api/v1/brands", { params: { query: { q } } })
      .then(({ data }) => (data ?? []).map((b) => ({ id: b.id, name: b.name })));
  }
  function createBrand(name: string): Promise<ComboOption> {
    return api.POST("/api/v1/brands", { body: { name } }).then(({ data, error }) => {
      if (!data) throw new Error(detail(error) ?? "建立品牌失敗");
      return { id: data.id, name: data.name };
    });
  }
  function searchModels(q: string): Promise<ComboOption[]> {
    return api
      .GET("/api/v1/product-models", {
        params: { query: { q, brand_id: row.brandId ?? undefined } },
      })
      .then(({ data }) => (data ?? []).map((m) => ({ id: m.id, name: m.name })));
  }
  function createModel(name: string): Promise<ComboOption> {
    if (row.brandId === null) return Promise.reject(new Error("請先選擇品牌"));
    return api
      .POST("/api/v1/product-models", { body: { brand_id: row.brandId, name } })
      .then(({ data, error }) => {
        if (!data) throw new Error(detail(error) ?? "建立型號失敗");
        return { id: data.id, name: data.name };
      });
  }

  return (
    <div className="card acq-row">
      <div className="acq-row-head">
        <span className="hint">第 {index + 1} 列</span>
        <button type="button" className="btn-ghost" onClick={onRemove}>
          移除
        </button>
      </div>
      <div className="acq-row-grid">
        <CreatableCombobox
          label="品牌"
          search={searchBrands}
          create={createBrand}
          placeholder="選擇或新增品牌"
          selectedId={row.brandId}
          onChange={(o) => onChange({ brandId: o?.id ?? null, productModelId: null })}
        />
        <CreatableCombobox
          label="型號"
          search={searchModels}
          create={createModel}
          placeholder={row.brandId === null ? "先選品牌" : "選擇或新增型號"}
          disabled={row.brandId === null}
          selectedId={row.productModelId}
          onChange={(o) => onChange({ productModelId: o?.id ?? null, ...(o ? { name: o.name } : {}) })}
        />
        <CreatableCombobox
          label="分類"
          search={(q) =>
            Promise.resolve(
              categories
                .filter((c) => c.name.toLowerCase().includes(q.toLowerCase()))
                .map((c) => ({ id: c.id, name: c.name })),
            )
          }
          create={(name) =>
            api.POST("/api/v1/categories", { body: { name } }).then(({ data, error }) => {
              if (!data) throw new Error(detail(error) ?? "建立分類失敗");
              refreshCategories();
              return { id: data.id, name: data.name };
            })
          }
          placeholder="選擇或新增分類"
          selectedId={row.categoryId}
          onChange={(o) => onChange({ categoryId: o?.id ?? null })}
        />
        <details className="acq-name-detail">
          <summary>品名：{row.name || "選型號自動帶入，或展開填寫"}</summary>
          <ItemNameField value={row.name} onChange={(name) => onChange({ name })} />
        </details>
        <label className="field">
          <span className="field-label">成色</span>
          <select aria-label="成色" value={row.grade} onChange={(e) => onChange({ grade: e.target.value as Grade })}>
            <option value="">請選擇</option>
            {SERIALIZED_GRADES.map((g) => (
              <option key={g} value={g}>
                {GRADE_LABEL[g]}
              </option>
            ))}
          </select>
        </label>
        {/* 定價前先看同款以前的行情：品牌＋型號都選了才查得到（見 PriceHint）。 */}
        <PriceHint
          brandId={row.brandId}
          productModelId={row.productModelId}
        />
        <details className="acq-legacy-pricing">
          <summary>{type === "BUYOUT" ? "其他估價方式（直接輸入未稅轉售價）" : "行情參考（不自動換算售價）"}</summary>
        <label className="field">
          <span className="field-label">
            {autoTaxApplies ? "估計轉售價（未稅）" : "估計轉售價"}
            <InfoTip
              text={
                autoTaxApplies
                  ? "你評估這件商品能為店裡賺回多少，也就是「不含稅、實際入袋」的金額。系統用它算出建議最高收購成本，並自動把加了稅的價格填到下面的上架售價。"
                  : "你評估這件大概能賣多少，只用來當參考。寄售的定價與分潤另有規則，這個數字不會自動帶到下面的上架售價。"
              }
            />
          </span>
          <input
            aria-label="估計轉售價"
            inputMode="numeric"
            value={row.estimatedResale}
            onChange={(e) => onChange({ estimatedResale: e.target.value, discount: "" })}
          />
        </label>
        </details>
      </div>

      {type === "BUYOUT" && (
        <div className="acq-quick-pricing">
          <label className="field">
            <span className="field-label">參考價（原價或目前最低價）</span>
            <input aria-label="參考價（原價或目前最低價）" inputMode="numeric"
              value={row.retailPrice} onChange={(e) => applyDiscount(e.target.value, row.discount)} />
          </label>
          <div className="acq-discounts" role="group" aria-label="預估可售折數">
            {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => (
              <button key={n} type="button" className="btn-secondary" aria-pressed={!customDiscount && row.discount === String(n)}
                onClick={() => { setCustomDiscount(false); applyDiscount(row.retailPrice, String(n)); }}>{n}折</button>
            ))}
            <button type="button" className="btn-secondary" aria-pressed={customDiscount}
              onClick={() => setCustomDiscount(true)}>自訂</button>
          </div>
          {customDiscount && <label className="field"><span className="field-label">自訂折數（0.1–10）</span>
            <input aria-label="自訂折數" inputMode="decimal" value={row.discount}
              onChange={(e) => applyDiscount(row.retailPrice, e.target.value)} />
            {row.discount && gradeFromDiscount(row.discount) === null && <span className="form-error">請輸入 0.1–10 折，最多一位小數。</span>}
          </label>}
          {/* 店主 2026-09-24：六折以上的二手價常是新品或近新品，提醒店員回頭確認成色。 */}
          {(discountPercent(row.discount) ?? 0) >= NEAR_NEW_DISCOUNT_PCT && (
            <p className="form-error acq-near-new" role="alert">
              {row.discount} 折偏高，可能是新品：請確認商品狀況並修改成色。
            </p>
          )}
          <p className="hint">也可不填參考價與折數，直接輸入上架售價，自動計算收購價；成色請自行選擇。折後價包含稅與手續費；自動售價進位至十元。{targetMargin !== null ? `目標毛利率：${targetMargin}%（可於設定維護）` : "正在讀取毛利設定"}。價格及成色皆可手動調整。</p>
        </div>
      )}

      {maxCost !== null && (
        <p className="acq-aid">
          建議最高收購成本：<strong className="money">{formatNtd(maxCost)}</strong>
          {category !== null && row.grade !== ""
            ? `（${category.name}・${gradeShortName(row.grade)}規則）`
            : ""}
        </p>
      )}

      {type === "BUYOUT" ? (
        <>
        <label className="field">
          <span className="field-label">收購價（每件）</span>
          <input
            aria-label="收購價"
            inputMode="numeric"
            value={row.acquisitionCost}
            onChange={(e) => onChange({ acquisitionCost: e.target.value, costManual: true })}
          />
          <button type="button" className="acq-link" onClick={() => onChange({ costManual: false })}>重新依毛利計算收購價</button>
          {overCost && <span className="form-error acq-warn">超過建議最高收購成本，毛利偏低</span>}
        </label>
        <label className="field acq-qty">
          <span className="field-label">件數</span>
          {/* 同款多件：客人一次帶三頂一樣的帳篷時不必按三次「新增一列」。
              送出時會展開成三筆各自獨立的商品（各有自己的條碼、可分別賣出）。 */}
          <input
            aria-label="件數"
            inputMode="numeric"
            value={row.qty}
            onChange={(e) => onChange({ qty: e.target.value })}
          />
          {qtyIssues.length > 0 ? (
            <span className="form-error">{qtyIssues[0]}</span>
          ) : (
            multiUnitTotal !== null && (
              <span className="hint">
                {`此列共 ${row.qty} 件，合計 ${formatNtd(multiUnitTotal)}`}
              </span>
            )
          )}
        </label>
        </>
      ) : (
        <label className="field">
          <span className="field-label">抽成 %（寄售）</span>
          <input
            inputMode="numeric"
            value={row.commissionPct === "" ? defaultCommissionPct : row.commissionPct}
            onChange={(e) => {
              const value = e.target.value;
              const editingDisplayedDefault =
                row.commissionPct === "" &&
                defaultCommissionPct !== "" &&
                value.startsWith(defaultCommissionPct);
              onChange({
                commissionPct: editingDisplayedDefault
                  ? value.slice(defaultCommissionPct.length)
                  : value,
              });
            }}
          />
        </label>
      )}

      <label className="field">
        <span className="field-label">
          上架售價（含稅與手續費）
          <InfoTip
            text={
              autoTaxApplies
                ? "客人最後支付的價格。可直接輸入，或依參考價與折數帶入；收購價依扣稅與支付費後的實得自動計算，手動修改過的收購價會保留。"
                : "客人實際要付的含稅價格，會存入系統並印在標籤上。寄售請直接輸入與寄售人談定的架上價。"
            }
          />
          {autoTaxApplies && !row.discount && category !== null && cost !== null && taxRate !== null && (
            <button
              type="button"
              className="acq-link"
              onClick={() =>
                onChange({
                  // suggestedListedPrice 本身已進位到 10 的倍數（ADR-023）。
                  listedPrice: String(
                    suggestedListedPrice(cost, category.target_margin_pct, taxRate, feeRate) ?? cost,
                  ),
                })
              }
            >
              套用建議（目標毛利 {category.target_margin_pct}%）
            </button>
          )}
          {/* 估計轉售價是未稅（店家實際入袋），上架售價是客人付的（含稅＋行動支付手續費）。
              輸入時已自動同步；這顆按鈕是給「手動改過之後想把價格再帶回來」用的。 */}
          {autoTaxApplies && resaleTaxInclusive !== null && (
            <button
              type="button"
              className="acq-link"
              onClick={() => onChange({ listedPrice: String(resaleTaxInclusive) })}
            >
              帶入客人實付價（{formatNtd(resaleTaxInclusive)}）
            </button>
          )}
        </span>
        <input
          aria-label="上架售價（含稅與手續費）"
          inputMode="numeric"
          value={row.listedPrice}
          onChange={(e) => onChange({ listedPrice: e.target.value })}
        />
        {autoTaxApplies && taxRateLoading ? (
          <span className="hint">正在讀取稅率設定，暫時無法自動換算含稅價。</span>
        ) : autoTaxApplies && taxRateUnavailable ? (
          // 沉默是最糟的：說明泡泡承諾「會自動加稅」，稅率讀不到時卻什麼都沒發生，
          // 店員多半就把心裡那個未稅數字直接打進去，每件少收一個稅額。
          <span className="form-error">
            讀不到稅率設定，無法自動換算含稅價——請直接輸入客人要付的含稅價格。
          </span>
        ) : (
          autoTaxApplies && margin !== null && (
            <span className="hint">
              毛利 {margin}%
              {targetMargin !== null && margin < targetMargin ? "（低於目標）" : ""}
            </span>
          )
        )}
      </label>

      {type === "BUYOUT" && netSale !== null && netProceeds !== null && (
        <details className="acq-price-breakdown">
          <summary>查看未稅價、手續費與實得</summary>
          <dl>
            <div><dt>未稅售價</dt><dd>{formatNtd(netSale)} 元</dd></div>
            <div><dt>預估支付手續費（{Math.round(feeRate * 10000) / 100}%）</dt><dd>{formatNtd(netSale - netProceeds)} 元</dd></div>
            <div><dt>扣稅、扣費後實得</dt><dd>{formatNtd(netProceeds)} 元</dd></div>
          </dl>
        </details>
      )}

      {/* 全新售價（原價，選填）：客人問「這值不值」時的對照數字。
          **純記錄**——不參與定價、毛利與報表的任何計算，查不到就留白。 */}
      {type !== "BUYOUT" && <label className="field">
        <span className="field-label">
          全新售價（原價，選填）
          <InfoTip text="這件商品全新時的市售價，用來跟客人說明二手價的落差。只是記錄，不會影響上架售價、毛利或報表。查不到就留白。" />
        </span>
        <input
          aria-label="全新售價（原價）"
          inputMode="numeric"
          placeholder="例：8000"
          value={row.retailPrice}
          onChange={(e) => onChange({ retailPrice: e.target.value })}
        />
      </label>}

      {/* 商品備註：驗機當下就記下狀況或作業提醒，結帳時系統會提醒店員。
          一列一則，套用該列全部件數（2026-09-04 裁示）——要分別註記就拆成多列填。 */}
      <label className="field acq-note">
        <span className="field-label">
          備註（選填）
          <InfoTip text="商品狀況或作業提醒，結帳時會跳出來提醒店員。例：缺充電線、右袖口磨損、先別賣等老闆確認。請勿填寫客人身分證或電話。" />
        </span>
        <input
          aria-label="商品備註"
          maxLength={NOTE_MAX_LENGTH}
          placeholder="例：缺營釘一支、附原廠盒"
          value={row.note}
          onChange={(e) => onChange({ note: e.target.value })}
        />
        {multiUnitCount !== null && multiUnitCount > 1 && row.note.trim() !== "" && (
          <span className="hint">這則備註會套用到本列全部 {multiUnitCount} 件。</span>
        )}
      </label>
    </div>
  );
}

/** 一件要印的標籤：品名/價格取自後端存下來的內容，品牌待解析。 */
type PendingLabel = {
  code: string;
  name: string;
  price: number;
  brandId: number | null;
  grade: components["schemas"]["Grade"];
};

/**
 * 品牌 id → 顯示名。
 *
 * 品項端點只回 `brand_id`，而品牌**沒有 by-id 端點**；改用篩選選項的「使用中品牌」——
 * 它不分狀態地列出所有掛著品項的品牌，所以剛收進來的這件，其品牌一定在裡面。
 *
 * **查不到就報錯，不默默印一張沒有品牌的標籤**：這裡的呼叫端已經確認有品項掛了品牌，
 * 缺的是名字。靜默省略那一行，店員會拿到一張看起來正常、實際少了資訊的標籤。
 */
async function brandNameMap(kind: "serialized" | "bulk"): Promise<Map<number, string>> {
  const path =
    kind === "serialized"
      ? ("/api/v1/serialized-items/filter-options" as const)
      : ("/api/v1/bulk-lots/filter-options" as const);
  const { data, error } = await api.GET(path, { params: { query: {} } });
  if (!data) throw new Error(detail(error) ?? "查不到品牌名稱，無法列印含品牌的標籤");
  return new Map(data.brands.map((b) => [b.id, b.name]));
}

/** 補上品牌名；沒有 brand_id 的品項回 null＝標籤上那一行整行不印（裁示 2026-09-14 第 2 點）。 */
async function resolveBrands(
  items: PendingLabel[],
  kind: "serialized" | "bulk",
): Promise<(string | null)[]> {
  if (items.every((i) => i.brandId === null)) return items.map(() => null);
  const brands = await brandNameMap(kind);
  return items.map((i) => {
    if (i.brandId === null) return null;
    const name = brands.get(i.brandId);
    if (!name?.trim()) throw new Error("查不到品牌名稱，請重新整理後再列印標籤");
    return name;
  });
}

// ── 標籤列印（Brother 標籤機）：收購完成後，逐一補印序號品 / 散裝批的條碼標籤 ──
// 右下角的全新／二手依成色決定：只有「全新未拆」印全新，其餘印二手（2026-09-16）；成色本身不印。
function PrintLabelsAction({
  codes,
  lot,
  basket = null,
  autoStart = false,
}: {
  codes: string[];
  lot: string | null;
  basket?: string | null;
  /** 設定「收購送出後自動印標籤」：畫面一出來就送印一次，店員不必再按。 */
  autoStart?: boolean;
}) {
  const total = codes.length + (lot !== null ? 1 : 0) + (basket !== null ? 1 : 0);

  const print = useMutation({
    mutationFn: async () => {
      const items: PendingLabel[] = [];
      for (const code of codes) {
        const { data, error } = await api.GET("/api/v1/serialized-items/by-code/{item_code}", {
          params: { path: { item_code: code } },
        });
        if (!data) throw new Error(detail(error) ?? `查無序號品 ${code}`);
        items.push({
          code,
          name: data.name,
          price: parseNtd(data.listed_price) ?? 0,
          brandId: data.brand_id,
          grade: data.grade,
        });
      }
      const lots: PendingLabel[] = [];
      if (lot !== null) {
        const { data, error } = await api.GET("/api/v1/bulk-lots/by-code/{lot_code}", {
          params: { path: { lot_code: lot } },
        });
        if (!data) throw new Error(detail(error) ?? `查無散裝 ${lot}`);
        lots.push({
          code: lot,
          name: data.name,
          price: parseNtd(data.unit_price) ?? 0,
          brandId: data.brand_id,
          grade: data.grade,
        });
      }
      if (basket !== null) {
        const { data, error } = await api.GET("/api/v1/bulk-baskets/by-code/{code}", {
          params: { path: { code: basket } },
        });
        if (!data) throw new Error(detail(error) ?? `查無販售籃 ${basket}`);
        lots.push({
          code: basket,
          name: data.name,
          price: parseNtd(data.unit_price) ?? 0,
          brandId: data.brand_id,
          grade: "E",
        });
      }

      // 品牌先全部解析完再開始送印：中途才發現查不到品牌，前面幾張已經印出去了，
      // 補印得重來一輪，標籤紙也白花了。
      const brands = [
        ...(await resolveBrands(items, "serialized")),
        ...(await resolveBrands(lots, "bulk")),
      ];

      const all = [...items, ...lots];
      for (const [i, it] of all.entries()) {
        await printLabel(it.code, it.name, it.price, {
          brand: brands[i],
          condition: labelConditionForGrade(it.grade),
        });
      }
      return all.length;
    },
  });

  // 只自動送一次。延到下一輪事件才送、卸下時取消：React 嚴格模式會先掛上→卸下→再掛上，
  // 若第一次掛上就送，結果綁在被卸掉的那個實例上，畫面會永遠停在「列印中」。
  const autoStarted = useRef(false);
  const { mutate: startPrint } = print;
  useEffect(() => {
    if (!autoStart || total === 0 || autoStarted.current) return;
    const timer = setTimeout(() => {
      autoStarted.current = true;
      startPrint();
    }, 0);
    return () => clearTimeout(timer);
  }, [autoStart, total, startPrint]);

  if (total === 0) return null;

  return (
    <div className="acq-print-labels">
      <button
        type="button"
        className="btn-secondary"
        onClick={() => print.mutate()}
        disabled={print.isPending}
      >
        {print.isPending
          ? "列印中…"
          : print.isSuccess
            ? `重新列印標籤（${total} 張）`
            : `列印標籤（${total} 張）`}
      </button>
      {print.isSuccess && (
        <p className="form-success">
          {autoStart ? "已自動送出列印" : "已送出"} {print.data} 張標籤。
        </p>
      )}
      {print.isError && (
        <p className="form-error">列印失敗：{print.error.message}</p>
      )}
    </div>
  );
}

/**
 * 收購憑證聯補印（docs/23 K6）。
 *
 * **原本印不回來**：憑證聯的列印按鈕在收購完成畫面的結果區塊裡，換一筆收購或離開頁面
 * 就消失——客人事後說「憑證聯不見了」店員無計可施。收購沒有清單頁（後端也沒有清單
 * 端點），所以這裡用單號查詢：店員從客人的收據或系統紀錄拿到單號即可補印。
 */
function ReprintAcquisitionReceipt() {
  const [input, setInput] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const reprint = useMutation({
    mutationFn: async () => {
      const id = Number(input.trim());
      if (!Number.isInteger(id) || id <= 0) throw new Error("請輸入收購單號（數字）");
      const { data, error } = await api.GET("/api/v1/acquisitions/{acquisition_id}/receipt", {
        params: { path: { acquisition_id: id } },
      });
      if (!data) {
        throw new Error(
          (error as { detail?: string } | undefined)?.detail ?? "找不到這張收購單",
        );
      }
      if (data.voided_at !== null) throw new Error("這張收購單已作廢，不補印憑證聯");
      // **憑證聯必有簽名**：代理端的版型無條件印「賣方簽名」那一欄
      // （`AcquisitionReceiptPrint.signature_png_base64` 是必填）。沒走手持切結的收購
      // 本來就沒有這張憑證聯，照實說明而不是印一張缺簽名的殘缺文件。
      if (data.signature_task_id === null) {
        throw new Error("這張收購當初沒有請客人於手持裝置簽名，沒有憑證聯可補印");
      }
      const signaturePngBase64 = await fetchSignaturePngBase64(data.signature_task_id);
      await printAcquisitionReceipt({
        storeId: data.store_id,
        acquisitionId: data.acquisition_id,
        sellerName: data.seller_name,
        items: data.items.map((i) => ({ name: i.name, amount: i.amount })),
        total: data.total,
        payoutMethod: data.payout_method,
        createdAt: data.created_at,
        signaturePngBase64,
        storeCreditGranted: data.store_credit_granted ?? undefined,
      });
      return data.acquisition_id;
    },
    onSuccess: (id) => setNote(`已送出 #${id} 的收購憑證聯。`),
    onError: (e: Error) => setNote(e.message),
  });

  // 標籤刻意不叫「收購單號」：頁面上已有同名欄位，撞名會讓自動化工具無法指定
  // （Playwright 嚴格模式直接拒絕），螢幕報讀軟體也分不出是哪一個。
  return (
    <div className="card acq-reprint">
      <h2>補印收購憑證聯</h2>
      <p className="hint">客人把憑證聯弄丟、或當初沒印到時，用收購單號補印一張。</p>
      <div className="acq-reprint-row">
        <input
          aria-label="要補印的收購單號"
          inputMode="numeric"
          placeholder="收購單號"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button
          type="button"
          className="btn-secondary"
          disabled={reprint.isPending || input.trim() === ""}
          onClick={() => {
            setNote(null);
            reprint.mutate();
          }}
        >
          {reprint.isPending ? "列印中…" : "補印"}
        </button>
      </div>
      {note !== null && (
        <p className={reprint.isError ? "form-error" : "hint"}>{note}</p>
      )}
    </div>
  );
}

export default function AcquisitionPage() {
  const queryClient = useQueryClient();
  const [type, setType] = useState<AcqType>("BUYOUT");
  const [seller, setSeller] = useState<Contact | null>(null);
  const [rows, setRows] = useState<Row[]>([emptyItem()]);
  const [lot, setLot] = useState<LotDraft>(emptyLot());
  // 收購①：買斷分頁再加的散裝（送出時拆成買斷一張、每堆散裝各一張；只簽一次、只付一次）。
  const [extraLots, setExtraLots] = useState<LotDraft[]>([]);
  // 每堆散裝一把穩定的 key：刪掉中間那堆時，後面的表單（含下拉選單內部狀態）不會錯位。
  const [extraLotKeys, setExtraLotKeys] = useState<string[]>([]);
  const [payoutMethod, setPayoutMethod] = useState<PayoutMethod>("CASH");
  const [splitCash, setSplitCash] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [result, setResult] = useState<{
    acquisitionId: number;
    /** 這筆的賣方：完成後可一鍵「繼續收這位賣方」，不必重新搜尋。 */
    seller: Contact | null;
    type: AcquisitionType;
    codes: string[];
    lot: string | null;
    /** 散裝入籃時的販售籃碼（標籤印這個）；未入籃為 null。 */
    basket: string | null;
    /** 加入的是既有籃：籃上已有標籤，不必重印。 */
    joinedBasket: boolean;
    /** 撥入購物金實發額（後端帳本分錄 signed_amount；非購物金撥款為 null）。 */
    creditGranted: string | null;
    /** 撥入後購物金總額（後端帳本分錄 balance_after；非購物金撥款為 null）。 */
    creditBalanceAfter: string | null;
    /** 買斷再加散裝時一起成立的散裝單（每堆一張）。 */
    extraLots: { acquisitionId: number; lot: string | null; basket: string | null; joinedBasket: boolean }[];
  } | null>(null);
  // 作廢剛建立的這筆（限管理者）：開啟確認對話框／顯示作廢結果。
  const [voidTarget, setVoidTarget] = useState<number | null>(null);
  // 送出成功後捲到完成卡片：送出鈕固定在畫面底部，卡片卻在頁尾，不捲過去看不到結果。
  const resultRef = useRef<HTMLDivElement>(null);
  const resultId = result?.acquisitionId ?? null;
  useEffect(() => {
    if (resultId !== null) resultRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [resultId]);
  const [voidedNote, setVoidedNote] = useState<string | null>(null);
  // 開錢櫃失敗提示（docs/10 §5：收購已成立，代理離線只提示、不可擋流程）。
  const [drawerNotice, setDrawerNotice] = useState<string | null>(null);
  // 送出成功後遞增 → 重掛鑑價列/散裝表單，連同 combobox 內部文字一併清空（避免顯示舊值卻無 id）。
  const [formKey, setFormKey] = useState(0);
  // 手持切結（docs/23 K4）：推送至手持裝置後的任務 id；輪詢其狀態，SIGNED 後才可完成收購，
  // 撥款方式以客人於手持端所選為準（D7）。
  const [signTaskId, setSignTaskId] = useState<number | null>(null);
  // 管理者才顯示作廢入口（後端 ManagerDep 為最終權威；前端隱藏僅為 UX）。token 在頁面生命週期內不變。
  const isManager = useMemo(() => decodeSession()?.role === "MANAGER", []);

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.GET("/api/v1/settings")).data ?? null,
  });
  const defaultCommissionPct =
    settings.data == null ? "" : String(settings.data.default_commission_pct);
  const rowsWithCommissionDefaults = useMemo(
    () =>
      rows.map((row) =>
        row.commissionPct === "" && defaultCommissionPct !== ""
          ? { ...row, commissionPct: defaultCommissionPct }
          : row,
      ),
    [defaultCommissionPct, rows],
  );
  const categoriesQuery = useQuery({
    queryKey: ["categories"],
    queryFn: async () =>
      (await api.GET("/api/v1/categories", { params: { query: { limit: 200 } } })).data ?? [],
  });
  const drawer = useQuery({
    queryKey: ["cash-session", "current"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/cash-sessions/current");
      return response.status === 200 ? (data ?? null) : null;
    },
  });
  // 手持切結任務狀態輪詢（等待客顯 ACK／簽署時每 2 秒；終態停）。
  // 完成收購時的簽署快照（K6 憑證聯列印用）：**全部取自已簽切結內容與簽署時間**（後端
  // 綁定驗證過的不可變值）——不用活的 UI 狀態/列印當下讀值，證據欄位不隨時間漂移
  //（Codex K6 第一輪）。
  interface ReceiptSnapshot {
    taskId: number;
    sellerName: string; // 簽署快照的 seller_name（後端以會員檔覆寫）
    items: { name: string; amount: string }[];
    total: string;
    payout: string;
    signedAt: string; // 簽署時間＝證據時點
  }
  const [receiptSnap, setReceiptSnap] = useState<ReceiptSnapshot | null>(null);
  const signTask = useQuery({
    queryKey: ["signing-task", signTaskId],
    enabled: signTaskId != null,
    refetchInterval: (q) =>
      q.state.data?.status === "PENDING" || q.state.data?.status === "SIGNING"
        ? 2000
        : false,
    queryFn: async () => {
      if (signTaskId == null) return null;
      const { data } = await api.GET("/api/v1/signing/tasks/{task_id}", {
        params: { path: { task_id: signTaskId } },
      });
      return data ?? null;
    },
  });
  const signed = signTask.data?.status === "SIGNED";
  const signTaskEnded =
    signTask.data?.status === "VOIDED" ||
    signTask.data?.status === "EXPIRED" ||
    signTask.data?.status === "FAILED";
  const signedPayout = signTask.data?.chosen_payout ?? null;

  const isConsignment = type === "CONSIGNMENT";
  const isBulk = type === "BULK_LOT";
  const combined = type === "BUYOUT" && extraLots.length > 0;
  const sellerIsMember = seller?.roles.includes("MEMBER") ?? false;
  const premiumRate = settings.data?.premium_rate ?? "0";
  // 營業稅率取自 settings（§6 不得寫死）。未載入或值不可用時一律為 null、不做含稅換算——
  // 若讓 NaN 流下去，上架售價欄位會被填成字串 "NaN"，比不自動帶入更糟。
  const rawTaxRate = settings.data ? Number(settings.data.tax_rate) : Number.NaN;
  const taxRate =
    Number.isFinite(rawTaxRate) && rawTaxRate >= 0 && rawTaxRate < 1 ? rawTaxRate : null;
  const taxRateLoading = !settings.isFetched;
  // 「還在載入」不等於「讀不到」：查詢尚未回來就喊錯誤，等於在後端慢或網路抖一下時
  // 叫店員自行加稅——他照做之後那筆就少收一個稅額，而且不會被之後的自動同步修正。
  const taxRateUnavailable = settings.isFetched && taxRate === null;
  // 行動支付手續費（裁示 2026-09-09）：標價要把它補回來，否則目標毛利達不到。
  // **取兩種支付的較高者**——定價當下不知道客人會刷哪一種，抓低的那個會少補。
  // 讀不到就當 0：寧可少補一點，也不要在設定沒載入時把價格墊高、客人多付。
  const feeRate = (() => {
    const rates = [settings.data?.linepay_fee_pct, settings.data?.taiwanpay_fee_pct]
      .map((r) => (r === undefined ? Number.NaN : Number(r)))
      .filter((r) => Number.isFinite(r) && r >= 0 && r < 1);
    return rates.length > 0 ? Math.max(...rates) : 0;
  })();
  const drawerOpen = drawer.data != null;

  const extraLotsPayable = combined
    ? extraLots.reduce((sum, extra) => sum + (parseNtd(extra.acquisitionCost) ?? 0), 0)
    : 0;
  const payable = isBulk
    ? parseNtd(lot.acquisitionCost) ?? 0
    : rowsPayableTotal(rows, type) + extraLotsPayable;  // 每列＝每件收購價 × 件數（非買斷一律 1 件）
  // 摘要列的件數：同款多件只在買斷有意義（寄售一列就是一件）；散裝看這批件數。
  const itemCount = isBulk
    ? parseNtd(lot.totalQty) ?? 0
    : rows.reduce(
        (sum, row) => sum + (type === "BUYOUT" ? Math.max(1, parseNtd(row.qty) ?? 1) : 1),
        0,
      ) + (combined ? extraLots.reduce((sum, extra) => sum + (parseNtd(extra.totalQty) ?? 0), 0) : 0);
  // 已簽切結 → 撥款以客人所選為準（D7），否則用店員選的（非手持流程）。
  const effectivePayout: PayoutMethod =
    signed && signedPayout ? signedPayout : payoutMethod;
  const creditEquiv =
    effectivePayout === "STORE_CREDIT"
      ? payable
      : effectivePayout === "SPLIT"
        ? Math.max(0, payable - (parseNtd(splitCash) ?? 0))
        : 0;
  const premiumGain = creditEquiv > 0 ? creditPremiumPreview(creditEquiv, premiumRate) : 0;

  const draft: AcquisitionDraft = {
    type,
    contactId: seller?.id ?? null,
    items: rowsWithCommissionDefaults,
    lot,
    payoutMethod,
    payoutSplitCash: splitCash,
    sellerIsMember,
  };

  // 收購冪等鍵凍結（Codex K4 第九/十八/十九輪）：同一次送出的重試須沿用同一把鍵，回應在 LAN
  // 途中遺失後（甚至重新整理/重掛）重送才能被後端冪等重放（而非以新鍵重複撥款）。鍵存
  // localStorage 為唯一事實來源、以 useSyncExternalStore 反映（hydration 安全，掛載即可得）。
  const pendingKey = useSyncExternalStore(
    subscribePendingAcqIdemKey,
    pendingAcqIdemKeySnapshot,
    pendingAcqIdemKeyServerSnapshot,
  );
  // 區分「本次掛載擁有的鍵」（送出失敗/曖昧後就地重試合法、不擋）與「先前掛載殘留的鍵」
  // （重掛後表單已空，殘留鍵須擋下送出、先核對，避免以相同內容靜默重放舊收購）。
  const [sessionOwnsKey, setSessionOwnsKey] = useState(false);
  // localStorage 寫入失敗（配額/隱私）：本 session 仍以記憶體後備防重複，但無法跨重整保護。
  const [idemNotDurable, setIdemNotDurable] = useState(false);
  const recoveryNeeded = pendingKey != null && !sessionOwnsKey;
  // 送出／送簽共用：散裝一堆、買斷品（同款多件展開成逐件）。
  const ntd = (value: string) => String(parseNtd(value));
  // 全新售價是選填：沒填就送 null，不要送 "null" 或 0——0 會被讀成「全新也不值錢」。
  const optionalNtd = (value: string) => {
    const parsed = parseNtd(value.trim());
    return parsed === null ? null : String(parsed);
  };
  const lotBody = (l: LotDraft) => ({
    name: l.name,
    acquisition_cost: ntd(l.acquisitionCost),
    acquisition_basis: l.acquisitionBasis,
    total_qty: parseNtd(l.totalQty),
    unit_price: ntd(l.unitPrice),
    retail_price: optionalNtd(l.retailPrice),
    brand_id: l.brandId,
    category_id: l.categoryId,
    label: l.label || null,
    note: l.note.trim() || null,
    // 販售籃（ADR-025）：沒選就不送，維持舊指紋與舊行為。
    ...(l.basketMode === "JOIN" && l.basketId !== null ? { basket_id: l.basketId } : {}),
    ...(l.basketMode === "NEW" ? { new_basket: true } : {}),
  });
  // 同款多件在此展開：一列填 3 件 → 送出 3 筆各自獨立的序號品。
  // 後端收的是純品項陣列（與店員按三次「新增一列」完全等價），不需要知道件數。
  const itemsBody = () =>
    expandByQty(rows, type).map((r) => ({
      name: r.name,
      grade: r.grade,
      listed_price: ntd(r.listedPrice),
      retail_price: optionalNtd(r.retailPrice),
      resale_discount_pct: discountPercent(r.discount ?? ""),
      brand_id: r.brandId,
      product_model_id: r.productModelId,
      category_id: r.categoryId,
      // 一列一則，展開後每件都帶同一則（2026-09-04 裁示）。空白送 null，
      // 避免建出「有備註但內容是空白」的商品害 POS 跳空提醒。
      note: r.note.trim() || null,
      ...(type === "BUYOUT"
        ? { acquisition_cost: ntd(r.acquisitionCost) }
        : r.commissionPct === ""
          ? {}
          : { commission_pct: parseNtd(r.commissionPct) }),
    }));

  const submit = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = { type, contact_id: seller?.id };
      if (isBulk) {
        body.lot = lotBody(lot);
      } else {
        body.items = itemsBody();
      }
      if (!isConsignment) {
        body.payout_method = effectivePayout;
        if (effectivePayout === "SPLIT") body.payout_split_cash = ntd(splitCash);
      }
      if (signed && signTaskId != null) body.signature_task_id = signTaskId;
      // 本次掛載已有鍵（就地重試，含記憶體後備）就沿用，否則新鑄並標記本掛載擁有。
      const key = loadPendingAcqIdemKey() ?? newIdempotencyKey();
      const durable = savePendingAcqIdemKey(key);
      setSessionOwnsKey(true);
      setIdemNotDurable(!durable); // 未持久化：提示跨重整保護不保證（本 session 仍防重複）。
      const { data, error, response } = combined
        ? await api
            .POST("/api/v1/acquisitions/combined", {
              body: {
                contact_id: seller?.id ?? 0,
                items: body.items as never,
                lots: extraLots.map(lotBody) as never,
                payout_method: effectivePayout === "STORE_CREDIT" ? "STORE_CREDIT" : "CASH",
                signature_task_id: signed && signTaskId != null ? signTaskId : null,
              },
              params: { header: { "Idempotency-Key": key } },
            })
            .then((res) => ({ ...res, data: res.data?.results }))
        : await api
            .POST("/api/v1/acquisitions", {
              body: body as never,
              params: { header: { "Idempotency-Key": key } },
            })
            .then((res) => ({ ...res, data: res.data ? [res.data] : undefined }));
      if (!data) {
        // 只有**非衝突的 4xx**（驗證/認證，確定未提交）才清鍵。409＝該鍵已屬先前已提交的
        // 收購（改了內容才會撞）→ 保留鍵，否則改表單再送會以新鍵重複建單/撥款；5xx/逾時/網路
        // 中斷屬曖昧亦保留鍵（Codex K4 第十六/十七輪）。網路中斷會在上面 throw、不走到這。
        if (canDiscardIdempotencyKey(response.status)) {
          clearPendingAcqIdemKey();
          setSessionOwnsKey(false);
        }
        throw new Error(detail(error) ?? "收購送出失敗");
      }
      return data;
    },
    onSuccess: (results) => {
      const data = results[0];
      const lotResults = results.slice(1);
      clearPendingAcqIdemKey(); // 本單完成，下一單換新冪等鍵
      setSessionOwnsKey(false);
      setIdemNotDurable(false);
      // 付現才開錢櫃（docs/10 §5：後端成功後才開櫃付款；寄售/純購物金不碰現金）。
      // fire-and-forget：收購已寫後端，開櫃失敗只提示、不擋流程。
      const cashPaid = isConsignment
        ? 0
        : effectivePayout === "CASH"
          ? payable
          : effectivePayout === "SPLIT"
            ? (parseNtd(splitCash) ?? 0)
            : 0;
      setDrawerNotice(null);
      if (cashPaid > 0) {
        openCashDrawer().catch((err: Error) => setDrawerNotice(err.message));
      }
      setResult({
        acquisitionId: data.acquisition_id,
        seller,
        type: data.type,
        codes: data.item_codes,
        lot: data.lot_code,
        basket: data.basket_code ?? null,
        joinedBasket: isBulk && lot.basketMode === "JOIN",
        // 買斷＋散裝一起收：購物金是分幾筆撥的，憑證聯印加總與最後一筆撥入後的總額。
        creditGranted: sumNtd(results.map((r) => r.payout_credit_granted)),
        creditBalanceAfter: results[results.length - 1].payout_credit_balance_after,
        extraLots: lotResults.map((r, i) => ({
          acquisitionId: r.acquisition_id,
          lot: r.lot_code,
          basket: r.basket_code ?? null,
          joinedBasket: extraLots[i]?.basketMode === "JOIN",
        })),
      });
      // 憑證聯快照（K6）：綁定簽署完成的收購才可列印憑證聯；值取自已簽切結內容
      // （後端於綁定時逐欄驗證過）。在清除 signTaskId/seller 前擷取。
      if (signed && signTaskId != null && signTask.data != null) {
        const c = signTask.data.content as Record<string, unknown>;
        const items = Array.isArray(c.items)
          ? (c.items as { name?: unknown; amount?: unknown }[]).map((it) => ({
              name: String(it.name ?? ""),
              amount: String(it.amount ?? ""),
            }))
          : [];
        setReceiptSnap({
          taskId: signTaskId,
          sellerName: String(c.seller_name ?? ""),
          items,
          total: String(c.total ?? ""),
          payout: String(signTask.data.chosen_payout ?? "CASH"),
          signedAt: String(signTask.data.signed_at ?? new Date().toISOString()),
        });
      } else {
        setReceiptSnap(null);
      }
      setVoidedNote(null);
      setRows([emptyItem()]);
      setLot(emptyLot());
      setExtraLots([]);
      setExtraLotKeys([]);
      setSeller(null);
      setSignTaskId(null); // 完成即解除手持切結綁定，下一單重新推送
      setFormKey((k) => k + 1);
      void queryClient.invalidateQueries({ queryKey: ["cash-session"] });
    },
    onError: (e: Error) => setErrors([e.message]),
  });

  // 憑證聯列印（docs/23 K6）：綁定簽署的收購完成後，印切結品項/總額/撥款＋賣方簽名。
  const [receiptNote, setReceiptNote] = useState<string | null>(null);
  const printReceipt = useMutation({
    mutationFn: async () => {
      if (receiptSnap == null || result == null) throw new Error("無可列印的憑證資料");
      // 品項/總額/撥款方式取自已簽快照（不可變）；撥入金額與購物金總額取後端收購回應的
      // 帳本分錄事實（signed_amount / balance_after，Codex 本輪：兩行同源才內部一致；
      // 後端以簽署凍結溢價率入帳，帳本值必等於客人所簽），不印列印當下另查的活餘額。
      // 簽名 PNG 為已簽任務原圖。
      const signaturePngBase64 = await fetchSignaturePngBase64(receiptSnap.taskId);
      await printAcquisitionReceipt({
        storeId: decodeSession()?.storeId ?? 1,
        acquisitionId: result.acquisitionId,
        sellerName: receiptSnap.sellerName,
        items: receiptSnap.items,
        total: receiptSnap.total,
        payoutMethod: receiptSnap.payout,
        createdAt: receiptSnap.signedAt,
        signaturePngBase64,
        storeCreditGranted: result.creditGranted ?? undefined,
        storeCreditBalanceAfter: result.creditBalanceAfter ?? undefined,
        reference:
          result.extraLots.length > 0
            ? `收購單 ${[result.acquisitionId, ...result.extraLots.map((l) => l.acquisitionId)]
                .map((id) => `#${id}`)
                .join("、")}`
            : null,
      });
    },
    onSuccess: () => setReceiptNote("憑證聯已送出列印"),
    onError: (e: Error) => setReceiptNote(e.message),
  });

  // 明確放棄未確認的收購鍵（店員已於收購紀錄核對確定「未建立」）：清鍵、解除掛載擁有並清空表單。
  const startFreshAcquisition = (): void => {
    clearPendingAcqIdemKey();
    setSessionOwnsKey(false);
    setIdemNotDurable(false);
    setErrors([]);
    setRows([emptyItem()]);
    setLot(emptyLot());
    setExtraLots([]);
    setExtraLotKeys([]);
    setSeller(null);
    setSignTaskId(null);
    setFormKey((k) => k + 1);
  };

  // 推送手持切結任務（docs/23 K4）：以當前鑑價內容建立 AFFIDAVIT 任務給手持裝置簽署。
  const pushSign = useMutation({
    mutationFn: async () => {
      if (!seller) throw new Error("請先選擇賣方");
      // 推簽前先跑與送出同一套驗證：否則有效列混著一列件數 0 時，客人會先簽到一份
      // 缺了那列的快照，等到最後按送出才被擋下——只能撤回簽署、請客人重簽一次
      // （Codex 第二輪對抗式審查）。擋在推簽前，客人只會簽一次。
      const problems = combined ? validateCombined(draft, extraLots) : validateDraft(draft);
      if (problems.length > 0) {
        setErrors(problems);
        throw new Error(problems[0]);
      }
      const items = isBulk
        ? [{ name: lot.name || "散裝", amount: String(payable) }]
        : // **與送出的 payload 用同一份展開結果**：後端綁定會逐項比對品名與金額，
          // 客人簽 1 件、店員改成 3 件會直接被擋下——件數因此自動被簽名綁住，
          // 不必另外傳一個件數欄位給後端比對（與散裝批的 lot 快照是不同做法）。
          expandByQty(rows, type).map((r) => ({
            name: r.name || "品項",
            amount: String(parseNtd(r.acquisitionCost) ?? 0),
          }));
      const content: Record<string, unknown> = {
        items,
        total: String(payable),
      };
      // 散裝批：把數量與計價基準納入簽署快照，綁定時精確比對——否則客人簽後仍可改 total_qty，
      // 建出客人未確認的數量存貨（Codex K4 第十一輪）。
      if (isBulk) {
        content.lot = {
          total_qty: parseNtd(lot.totalQty),
          acquisition_basis: lot.acquisitionBasis,
        };
      }
      const terminalResponse = await api.POST("/api/v1/customer-display/terminals", {
        body: {
          installation_id: terminalInstallationId(),
          name: "主要櫃檯",
        },
      });
      const terminal = terminalResponse.data;
      if (!terminal?.paired_kiosk) {
        throw new Error("請先將此 POS 櫃檯與顧客螢幕配對");
      }
      if (!terminal.paired_kiosk.online) {
        throw new Error("顧客螢幕目前離線，無法進行收購簽署");
      }
      if (combined) {
        // 買斷＋散裝一起收：簽署內容由後端依同一份資料產生，送出時後端再精確比對。
        const combo = await api.POST("/api/v1/acquisitions/combined/affidavit", {
          body: {
            contact_id: seller.id,
            items: itemsBody() as never,
            lots: extraLots.map(lotBody) as never,
            terminal_id: terminal.id,
          },
        });
        if (!combo.data) throw new Error(detail(combo.error) ?? "推送手持簽署失敗");
        return { id: combo.data.id };
      }
      const { data, error } = await api.POST("/api/v1/signing/tasks", {
        body: {
          kind: "ACQUISITION_AFFIDAVIT",
          contact_id: seller.id,
          content,
          terminal_id: terminal.id,
          ref_type: "acquisition",
        },
      });
      if (!data) throw new Error(detail(error) ?? "推送手持簽署失敗");
      return data;
    },
    onSuccess: (d) => {
      setErrors([]);
      setSignTaskId(d.id);
    },
    onError: (e: Error) => setErrors([e.message]),
  });
  const cancelSign = useMutation({
    mutationFn: async () => {
      if (signTaskId == null) return;
      const { response } = await api.POST("/api/v1/signing/tasks/{task_id}/cancel", {
        params: { path: { task_id: signTaskId } },
        body: {
          reason_code: "CONTENT_CHANGED",
          reason: "撤回收購簽署並修改鑑價內容",
        },
      });
      // 非 2xx 不可視為取消成功而清除綁定：SIGNED 在成交前可依規格作廢；
      // 只有 CONSUMED 等終態會拒絕，屆時重新輪詢並保留 signTaskId。
      if (!response.ok) {
        await signTask.refetch();
        throw new Error("此簽署已進入不可撤回狀態，請確認收購是否已成立");
      }
    },
    onSuccess: () => setSignTaskId(null), // 僅確認 VOIDED（2xx）才解除綁定
    onError: (e: Error) => setErrors([e.message]),
  });

  function onSubmit() {
    // 有先前掛載殘留、未確認的收購鍵時，先擋下送出、要求核對（Codex K4 第十九輪）。
    if (recoveryNeeded) {
      setErrors(["有一筆未確認的收購，請先至收購紀錄核對；確定未建立再按「開新單」"]);
      return;
    }
    setErrors([]);
    setResult(null);
    const found = combined ? validateCombined(draft, extraLots) : validateDraft(draft);
    if (!isConsignment && (payoutMethod === "CASH" || payoutMethod === "SPLIT") && !drawerOpen) {
      found.push("現金/混合撥款需先開帳（前往現金對帳開帳）");
    }
    if (found.length > 0) {
      setErrors(found);
      // 出錯的列若是收合的，展開它：錯誤訊息只在頁尾、那列卻是一行摘要，店員找不到要改哪裡。
      // 以錯誤訊息的「第 N 列」判斷，任何列層級的檢查（含件數）都涵蓋到。
      const badRows = new Set(
        found.flatMap((message) => {
          const match = /^第 (\d+) 列/.exec(message);
          return match ? [Number(match[1]) - 1] : [];
        }),
      );
      if (!isBulk && badRows.size > 0) {
        setRows((prev) =>
          prev.map((row, index) =>
            row.collapsed && badRows.has(index) ? { ...row, collapsed: false } : row,
          ),
        );
      }
      return;
    }
    submit.mutate();
  }

  // 以 rowKey（非 index）定位：品牌/型號的「建立」是非同步的，若在等待期間刪掉前面的列，
  // 捕獲了舊 index 的回呼會把回來的 id 寫進「現在佔用該 index」的別列——品牌/型號為選填，
  // 這種錯置不會被驗證擋下，會靜默把商品資訊掛到錯的品項上。
  function patchRow(rowKey: string, patch: Partial<Row>) {
    setRows((prev) => prev.map((r) => (r.rowKey === rowKey ? { ...r, ...patch } : r)));
  }

  return (
    <section className="acq">
      <h1 className="page-title">收購鑑價入庫</h1>

      <fieldset
        className="acq-signature-lock"
        disabled={signTaskId !== null && !signTaskEnded}
      >
        {signTaskId !== null && !signTaskEnded && (
          <p className="hint" aria-live="polite">
            簽署任務進行中，鑑價內容已凍結；如需修改請先撤回簽署。
          </p>
        )}
      <div className="acq-types" role="tablist">
        {(["BUYOUT", "CONSIGNMENT", "BULK_LOT"] as AcqType[]).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={type === t}
            className={type === t ? "inv-tab inv-tab-active" : "inv-tab"}
            onClick={() => {
              setType(t);
              setResult(null);
              setErrors([]);
            }}
          >
            {ACQ_TYPE_LABEL[t]}
          </button>
        ))}
      </div>

      <SellerSection seller={seller} onSelect={setSeller} />

      {isBulk ? (
        <BulkLotForm
          key={formKey}
          lot={lot}
          categories={categoriesQuery.data ?? []}
          onChange={setLot}
        />
      ) : (
        <div className="acq-rows">
          {rows.map((row, i) => (
            // 收合時列不卸掉、只隱藏：下拉選單顯示的名稱只存在元件裡，卸掉再掛回來會變空白，
            // 畫面看起來沒選、送出的 id 卻還在（Codex 審查）。
            <div key={row.rowKey}>
              {row.collapsed && (
                <CollapsedRow
                  index={i}
                  row={row}
                  type={type}
                  onExpand={() => patchRow(row.rowKey, { collapsed: false })}
                />
              )}
              <div hidden={row.collapsed}>
            <ItemRowCard
              type={type}
              index={i}
              row={row}
              categories={categoriesQuery.data ?? []}
              onChange={(patch) => patchRow(row.rowKey, patch)}
              onRemove={() =>
                setRows((prev) => prev.filter((r) => r.rowKey !== row.rowKey))
              }
              refreshCategories={() =>
                void queryClient.invalidateQueries({ queryKey: ["categories"] })
              }
              defaultCommissionPct={defaultCommissionPct}
              taxRate={taxRate}
              defaultMarginPct={settings.data?.default_margin_pct ?? null}
              feeRate={feeRate}
              taxRateLoading={taxRateLoading}
              taxRateUnavailable={taxRateUnavailable}
            />
              </div>
            </div>
          ))}
          <button
            type="button"
            className="acq-add-row"
            onClick={() =>
              // 已填品名的列收合成一行摘要：一次收多件時不必一路往下捲。空白列保持展開。
              setRows((p) => [
                ...p.map((r) => (r.name.trim() ? { ...r, collapsed: true } : r)),
                emptyItem(),
              ])
            }
          >
            ＋ 新增一列
          </button>
        </div>
      )}

      {type === "BUYOUT" && extraLots.length === 0 && (
        // 沒有散裝時只留一顆小按鈕，不佔版面；按了才展開散裝區。
        <button
          type="button"
          className="acq-add-row acq-add-row-minor"
          onClick={() => {
            setExtraLots([emptyLot()]);
            setExtraLotKeys([newIdempotencyKey()]);
          }}
        >
          ＋ 同一位客人還有散裝
        </button>
      )}

      {type === "BUYOUT" && extraLots.length > 0 && (
        <div className="card acq-extra-lots" aria-label="一起收的散裝">
          <h2>同一位客人還有散裝？</h2>
          <p className="hint">
            一起收、客人只簽一次名、只付一次錢；送出後會分成買斷一張、散裝每堆一張（作廢與報表照單張算）。
            一起收時撥款只能全付現金或全給購物金。
          </p>
          {extraLots.map((extra, i) => (
            <div key={extraLotKeys[i]} className="acq-extra-lot">
              <div className="acq-row-head">
                <span className="hint">第 {i + 1} 堆散裝</span>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => {
                    setExtraLots((prev) => prev.filter((_, j) => j !== i));
                    setExtraLotKeys((prev) => prev.filter((_, j) => j !== i));
                  }}
                >
                  移除這堆
                </button>
              </div>
              <BulkLotForm
                lot={extra}
                categories={categoriesQuery.data ?? []}
                onChange={(next) => setExtraLots((prev) => prev.map((l, j) => (j === i ? next : l)))}
              />
            </div>
          ))}
          <button
            type="button"
            className="acq-add-row acq-add-row-minor"
            onClick={() => {
              setExtraLots((prev) => [...prev, emptyLot()]);
              setExtraLotKeys((prev) => [...prev, newIdempotencyKey()]);
            }}
          >
            ＋ 再加一堆散裝
          </button>
        </div>
      )}

      {!isConsignment && (
        <div className="card acq-payout">
          <h2>撥款</h2>
          <div className="acq-payout-modes">
            {(["CASH", "STORE_CREDIT", "SPLIT"] as PayoutMethod[]).map((m) => (
              <label key={m} className="acq-payout-mode">
                <input
                  type="radio"
                  name="payout"
                  checked={effectivePayout === m}
                  disabled={signed || (m === "SPLIT" && combined)}
                  onChange={() => setPayoutMethod(m)}
                />
                {PAYOUT_LABEL[m]}
              </label>
            ))}
          </div>
          {signed && (
            <p className="form-success">
              客人已於手持裝置選擇撥款：{signedPayout ? PAYOUT_LABEL[signedPayout] : "—"}
            </p>
          )}
          <p>
            應付現金總額：<strong className="money">{formatNtd(payable)}</strong>
          </p>
          {effectivePayout === "SPLIT" && (
            <label className="field">
              <span className="field-label">現金部分</span>
              <input inputMode="numeric" value={splitCash} onChange={(e) => setSplitCash(e.target.value)} />
            </label>
          )}
          {(effectivePayout === "STORE_CREDIT" || effectivePayout === "SPLIT") && (
            <p className="acq-premium">
              {sellerIsMember
                ? `購物金入帳 ${formatNtd(creditEquiv + premiumGain)}（含溢價可多得 ${formatNtd(premiumGain)}，依當前溢價率試算）`
                : "提醒：購物金/混合撥款的對象必須是會員"}
            </p>
          )}
          {(effectivePayout === "CASH" || effectivePayout === "SPLIT") && !drawerOpen && (
            <p className="form-error">尚未開帳：現金/混合撥款需先至「現金對帳」開帳</p>
          )}
        </div>
      )}
      </fieldset>

      {/* 手持切結（docs/23 K4）：BUYOUT/BULK_LOT 可送至手持裝置請客人確認切結＋撥款＋簽名 */}
      {!isConsignment && (
        <div className="card acq-sign">
          <h2>手持簽署</h2>
          {signTaskId == null ? (
            <button
              type="button"
              className="btn-secondary"
              disabled={seller == null || payable <= 0 || pushSign.isPending}
              onClick={() => pushSign.mutate()}
            >
              送至手持裝置簽署
            </button>
          ) : signTaskEnded ? (
            <div className="acq-sign-wait">
              <p role="alert" className="form-error">
                {signTask.data?.status === "EXPIRED"
                  ? "客人太久沒有簽名，請重新送出給客人簽。"
                  : signTask.data?.status === "FAILED"
                    ? "此簽署已標記失敗，重試必須重新簽署。"
                    : "簽署已撤回。"}
              </p>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setSignTaskId(null)}
              >
                建立新簽署
              </button>
            </div>
          ) : signed ? (
            <div className="acq-sign-wait">
              <p className="form-success">✓ 客人已完成簽署，可送出收購。</p>
              <button
                type="button"
                className="btn-ghost"
                disabled={cancelSign.isPending}
                onClick={() => cancelSign.mutate()}
              >
                撤回簽署並修改
              </button>
            </div>
          ) : (
            <div className="acq-sign-wait">
              <p>
                {signTask.data?.status === "SIGNING"
                  ? "客人正在核對內容並簽署…"
                  : "已送至顧客螢幕，等待客人開啟簽署畫面…"}
              </p>
              <button
                type="button"
                className="btn-ghost"
                disabled={cancelSign.isPending}
                onClick={() => cancelSign.mutate()}
              >
                撤回簽署並修改
              </button>
            </div>
          )}
        </div>
      )}

      {errors.length > 0 && (
        <ul className="form-error acq-errors" role="alert">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}

      {idemNotDurable && !recoveryNeeded && (
        <p className="form-error acq-idem-warn" role="alert">
          注意：本機瀏覽器儲存異常，未完成收購僅在本頁面有效、無法跨重新整理保護；送出後請勿
          重整頁面，若疑似未成功請於收購紀錄確認後再處理。
        </p>
      )}

      {recoveryNeeded && (
        <div className="acq-pending-recovery" role="alert">
          <p>
            偵測到一筆先前<strong>未確認的收購（可能已完成）</strong>——本機在送出後未收到成功
            回應（斷線／逾時／頁面重整）。請先至<strong>收購紀錄</strong>確認是否已建立：
            若已建立，請勿重送以免重複；若<strong>確定未建立</strong>，再按「開新單」重新收購。
            （在此之前已停用送出，避免以相同內容靜默重放舊收購。）
          </p>
          <button type="button" className="btn-secondary" onClick={startFreshAcquisition}>
            確定未建立，開新單
          </button>
        </div>
      )}

      {/* 底部固定摘要：件數與應付一直看得到，送出不必捲到最底。 */}
      <div className="card acq-summary-bar" role="region" aria-label="收購摘要">
        <p>
          共 {itemCount} 件
          {!isConsignment && (
            <>
              ・應付 <strong className="money">{formatNtd(payable)}</strong>
            </>
          )}
        </p>
        <button
          type="button"
          className="btn-primary acq-submit"
          onClick={onSubmit}
          disabled={submit.isPending || recoveryNeeded || (signTaskId != null && !signed)}
        >
          送出收購
        </button>
      </div>

      {result !== null && (
        <div className="card form-success acq-result" ref={resultRef}>
          <p>
            {result.extraLots.length > 0
              ? `收購完成：買斷 #${result.acquisitionId}、散裝 ${result.extraLots.map((l) => `#${l.acquisitionId}`).join("、")}。`
              : `收購完成（單號 #${result.acquisitionId}）。`}
          </p>
          {drawerNotice !== null && (
            <p role="alert" className="form-error">
              錢櫃未開啟：{drawerNotice}（收購已完成，請以鑰匙開櫃付款）
            </p>
          )}
          {result.codes.length > 0 && <p>序號條碼：{result.codes.join("、")}</p>}
          {result.lot !== null && <p>散裝編號：{result.lot}</p>}
          {result.basket !== null && <p>販售籃：{result.basket}</p>}
          {result.basket !== null && result.joinedBasket ? (
            <p className="hint">已加入販售籃，沿用籃上原本的標籤，不必重印。</p>
          ) : null}
          <PrintLabelsAction
            autoStart={settings.data?.auto_print_acquisition_labels ?? false}
            codes={result.codes}
            // 入籃的散裝貼籃子的標籤（多次收購共用一張），不印這批自己的。
            lot={result.basket === null ? result.lot : null}
            basket={result.basket !== null && !result.joinedBasket ? result.basket : null}
          />
          {result.extraLots.map((extra) => (
            <div key={extra.acquisitionId}>
              <p>
                散裝 #{extra.acquisitionId}：{extra.basket !== null ? `販售籃 ${extra.basket}` : `散裝編號 ${extra.lot}`}
                {extra.basket !== null && extra.joinedBasket ? "（沿用籃上原本的標籤，不必重印）" : ""}
              </p>
              <PrintLabelsAction
                autoStart={settings.data?.auto_print_acquisition_labels ?? false}
                codes={[]}
                lot={extra.basket === null ? extra.lot : null}
                basket={extra.basket !== null && !extra.joinedBasket ? extra.basket : null}
              />
            </div>
          ))}
          {receiptSnap !== null && (
            <div className="acq-receipt-print">
              <button
                type="button"
                className="btn-secondary"
                disabled={printReceipt.isPending}
                onClick={() => printReceipt.mutate()}
              >
                {printReceipt.isPending ? "列印中…" : "列印收購憑證聯（含簽名）"}
              </button>
              {receiptNote !== null && <p className="hint">{receiptNote}</p>}
            </div>
          )}
          {result.seller !== null && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setSeller(result.seller);
                setResult(null);
              }}
            >
              繼續收這位賣方（{result.seller.name}）
            </button>
          )}
          {result.extraLots.length > 0 && (
            <p className="hint">這次分成好幾張收購單；要作廢請到「收購紀錄」逐張處理。</p>
          )}
          {voidedNote === null && isManager && result.extraLots.length === 0 && canVoid({ voided_at: null, type: result.type }) && (
            <button
              type="button"
              className="btn-danger acq-void-after-create"
              onClick={() => setVoidTarget(result.acquisitionId)}
            >
              這筆有誤？作廢收購
            </button>
          )}
          {voidedNote !== null && <p className="form-error">{voidedNote}</p>}
        </div>
      )}

      {voidTarget !== null && (
        <VoidConfirmDialog
          acquisitionId={voidTarget}
          onClose={() => setVoidTarget(null)}
          onVoided={(r) => {
            setVoidTarget(null);
            setVoidedNote(
              `已作廢收購單 #${r.acquisition_id}（退回現金 ${formatNtd(parseNtd(r.reversed_cash) ?? 0)}、沖回購物金 ${formatNtd(parseNtd(r.reversed_credit) ?? 0)}）。`,
            );
          }}
        />
      )}

      {/* 少用的功能收在最下面，頁面一打開就是收購表單。 */}
      <details className="acq-more">
        <summary>更多操作：補印收購憑證聯、查看過去的收購</summary>
        <ReprintAcquisitionReceipt />
        {/* 作廢只剩收購紀錄一個入口（2026-09-23）：不必記單號，清單上直接按。 */}
        <p className="acq-records-link">
          查看過去的收購{isManager ? "或作廢" : ""}：<Link href="/acquisition/records">收購紀錄</Link>
        </p>
      </details>
    </section>
  );
}

// ── 散裝販售籃選擇（ADR-025）──
function BasketPicker({
  baskets,
  loading,
  failed,
  chosen,
  onChoose,
}: {
  baskets: BulkBasket[];
  loading: boolean;
  failed: boolean;
  chosen: BulkBasket | null;
  onChoose: (basket: BulkBasket | null) => void;
}) {
  if (failed) return <p className="form-error">販售籃讀取失敗，請稍後再試或改選「開新販售籃」。</p>;
  if (loading) return <p className="hint">讀取販售籃中…</p>;
  if (baskets.length === 0) return <p className="hint">還沒有販售籃，請改選「開新販售籃」。</p>;
  const ref = chosen?.cost_reference;
  const costRange =
    ref && ref.unit_cost_min != null && ref.unit_cost_max != null
      ? ref.unit_cost_min === ref.unit_cost_max
        ? `${formatNtd(parseNtd(ref.unit_cost_min) ?? 0)}`
        : `${formatNtd(parseNtd(ref.unit_cost_min) ?? 0)}–${formatNtd(parseNtd(ref.unit_cost_max) ?? 0)}`
      : null;
  return (
    <>
      <label className="field">
        <span className="field-label">選擇販售籃</span>
        <select
          aria-label="販售籃"
          value={chosen?.id ?? ""}
          onChange={(e) =>
            onChoose(baskets.find((b) => String(b.id) === e.target.value) ?? null)
          }
        >
          <option value="">請選擇</option>
          {baskets.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}（每件 {formatNtd(parseNtd(b.unit_price) ?? 0)} 元）
            </option>
          ))}
        </select>
      </label>
      {chosen !== null ? (
        <p className="hint">
          目前 {chosen.remaining_qty} 件・每件 {formatNtd(parseNtd(chosen.unit_price) ?? 0)} 元。
          {costRange === null
            ? "還沒有收購紀錄可參考。"
            : `以前收過 ${ref?.sample_count ?? 0} 批，單件收購成本 ${costRange} 元（整批成本 ÷ 件數）。`}
        </p>
      ) : null}
    </>
  );
}

// ── 散裝批 ──
function BulkLotForm({
  lot,
  categories,
  onChange,
}: {
  lot: LotDraft;
  categories: Category[];
  onChange: (lot: LotDraft) => void;
}) {
  function patch(p: Partial<LotDraft>) {
    onChange({ ...lot, ...p });
  }
  const joining = lot.basketMode === "JOIN";
  const baskets = useQuery({
    queryKey: ["bulk-baskets"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/bulk-baskets");
      if (!data) throw new Error(detail(error) ?? "讀取販售籃失敗");
      return data;
    },
    enabled: joining,
  });
  const chosen = joining ? baskets.data?.find((b) => b.id === lot.basketId) ?? null : null;

  function chooseMode(mode: LotDraft["basketMode"]) {
    // 離開「加入」時只解除鎖定、不清掉已帶入的內容：店員可能只是想以這批另開一籃。
    onChange({ ...lot, basketMode: mode, basketId: mode === "JOIN" ? lot.basketId : null });
  }

  function chooseBasket(basket: BulkBasket | null) {
    if (basket === null) {
      patch({ basketId: null });
      return;
    }
    // 同籃同品項同價（店主 2026-09-22 裁示）：名稱／品牌／分類／售價一律以籃子為準。
    patch({
      basketId: basket.id,
      name: basket.name,
      brandId: basket.brand_id,
      categoryId: basket.category_id,
      unitPrice: String(parseNtd(basket.unit_price) ?? ""),
    });
  }

  return (
    <div className="card acq-row">
      <h2>散裝</h2>
      <fieldset className="acq-basket">
        <legend className="field-label">
          販售籃
          <InfoTip text="同樣的東西（例如無品牌營釘）不同客人分次賣進來，可以放同一籃、貼同一張標籤、賣同一個價。每次收購的成本和數量仍各自記錄。" />
        </legend>
        <label>
          <input
            type="radio"
            name="basket-mode"
            checked={lot.basketMode === "NONE"}
            onChange={() => chooseMode("NONE")}
          />
          不放入販售籃
        </label>
        <label>
          <input
            type="radio"
            name="basket-mode"
            checked={lot.basketMode === "NEW"}
            onChange={() => chooseMode("NEW")}
          />
          開新販售籃
        </label>
        <label>
          <input
            type="radio"
            name="basket-mode"
            checked={joining}
            onChange={() => chooseMode("JOIN")}
          />
          加入現有販售籃
        </label>
        {lot.basketMode === "NEW" ? (
          <p className="hint">
            送出後會開一個新籃並印籃子的標籤；之後同樣的東西，選「加入現有販售籃」就能共用這張標籤。
          </p>
        ) : null}
        {joining ? (
          <BasketPicker
            baskets={baskets.data ?? []}
            loading={baskets.isLoading}
            failed={baskets.isError}
            chosen={chosen}
            onChoose={chooseBasket}
          />
        ) : null}
      </fieldset>
      <div className="acq-row-grid">
        <label className="field">
          <span className="field-label">名稱</span>
          <input
            aria-label="名稱"
            value={lot.name}
            readOnly={joining}
            onChange={(e) => patch({ name: e.target.value })}
          />
        </label>
        {joining ? (
          <p className="hint acq-basket-locked">品牌、分類、每件售價沿用販售籃，要改請到庫存頁改販售籃。</p>
        ) : (
          <>
        <CreatableCombobox
          label="品牌"
          search={(q) =>
            api
              .GET("/api/v1/brands", { params: { query: { q } } })
              .then(({ data }) => (data ?? []).map((b) => ({ id: b.id, name: b.name })))
          }
          create={(name) =>
            api.POST("/api/v1/brands", { body: { name } }).then(({ data, error }) => {
              if (!data) throw new Error(detail(error) ?? "建立品牌失敗");
              return { id: data.id, name: data.name };
            })
          }
          placeholder="選擇或新增品牌"
          selectedId={lot.brandId}
          onChange={(o) => patch({ brandId: o?.id ?? null })}
        />
        <CreatableCombobox
          label="分類（選填）"
          search={(q) =>
            Promise.resolve(
              categories
                .filter((c) => c.name.toLowerCase().includes(q.toLowerCase()))
                .map((c) => ({ id: c.id, name: c.name })),
            )
          }
          create={(name) =>
            api.POST("/api/v1/categories", { body: { name } }).then(({ data, error }) => {
              if (!data) throw new Error(detail(error) ?? "建立分類失敗");
              return { id: data.id, name: data.name };
            })
          }
          placeholder="選擇或新增分類"
          selectedId={lot.categoryId}
          onChange={(o) => patch({ categoryId: o?.id ?? null })}
        />
          </>
        )}
        <label className="field">
          <span className="field-label">整堆收購成本</span>
          <input
            inputMode="numeric"
            value={lot.acquisitionCost}
            onChange={(e) => patch({ acquisitionCost: e.target.value })}
          />
        </label>
        <label className="field">
          <span className="field-label">收購基準</span>
          <select
            value={lot.acquisitionBasis}
            onChange={(e) =>
              patch({ acquisitionBasis: e.target.value as LotDraft["acquisitionBasis"] })
            }
          >
            <option value="">請選擇</option>
            <option value="WEIGHT">秤斤</option>
            <option value="BAG">整袋</option>
          </select>
        </label>
        <label className="field">
          <span className="field-label">件數</span>
          <input inputMode="numeric" value={lot.totalQty} onChange={(e) => patch({ totalQty: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">每件均一價</span>
          <input
            aria-label="每件均一價"
            inputMode="numeric"
            value={lot.unitPrice}
            readOnly={joining}
            onChange={(e) => patch({ unitPrice: e.target.value })}
          />
        </label>
        {/* 全新售價（原價，選填）：純記錄，不參與定價、毛利與報表的任何計算。 */}
        <label className="field">
          <span className="field-label">
            全新售價（原價，選填）
            <InfoTip text="這批商品全新時的市售價，用來跟客人說明二手價的落差。只是記錄，不會影響每件均一價、毛利或報表。查不到就留白。" />
          </span>
          <input
            aria-label="全新售價（原價）"
            inputMode="numeric"
            placeholder="例：150"
            value={lot.retailPrice}
            onChange={(e) => patch({ retailPrice: e.target.value })}
          />
        </label>
        <label className="field">
          <span className="field-label">命名（選填）</span>
          <input value={lot.label} onChange={(e) => patch({ label: e.target.value })} />
        </label>
        {/* 散裝批同樣可在收購當下寫備註（三種庫存型態一致）。 */}
        <label className="field acq-note">
          <span className="field-label">
            備註（選填）
            <InfoTip text="商品狀況或作業提醒，結帳時會跳出來提醒店員。例：數量請客人自己點過、放 B 架第三層。請勿填寫客人身分證或電話。" />
          </span>
          <input
            aria-label="散裝備註"
            maxLength={NOTE_MAX_LENGTH}
            placeholder="例：數量請客人自己點過"
            value={lot.note}
            onChange={(e) => patch({ note: e.target.value })}
          />
        </label>
      </div>
    </div>
  );
}
