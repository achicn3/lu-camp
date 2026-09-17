"use client";
// /menu 餐飲菜單管理頁（MANAGER 專用）：品項清單（含停售）＋ 建立 ＋ 改名改價/上下架/封存。
// 純呈現：金額為整數元字串，走 OpenAPI 生成 client（禁手刻型別）。餐飲不扣庫存、不折活動。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { marginPct, suggestedListedPrice } from "@/features/acquisition/pricing";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";
import { useCurrentRole } from "@/lib/useCurrentRole";

type MenuItemRead = components["schemas"]["MenuItemRead"];

/**
 * 定價用的店內設定。兩個費率刻意分開，因為兩邊的「安全方向」相反：
 * - `feeRateForPricing`：算建議售價用，讀不到就以 0 計（CLAUDE.md §7.9：寧可少補，
 *   也不要在設定沒載入時把客人的價格墊高）。
 * - `feeRate`：顯示既有品項的預估毛利率用，讀不到就回 null 而不顯示——這裡用 0 會
 *   把被金流商抽走的錢算成店家收益，毛利系統性高估。
 */
type PricingRates = {
  taxRate: number | null;
  feeRate: number | null;
  feeRateForPricing: number;
  defaultMargin?: number;
};

function usePricingRates(enabled: boolean): PricingRates {
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.GET("/api/v1/settings")).data ?? null,
    enabled,
  });
  const taxValue = settings.data?.tax_rate;
  const rawTax = taxValue != null && String(taxValue).trim() !== "" ? Number(taxValue) : Number.NaN;
  const taxRate = !settings.isError && Number.isFinite(rawTax) && rawTax >= 0 && rawTax < 1 ? rawTax : null;
  // 手續費取兩種行動支付的較高者（定價當下不知道客人會刷哪種，取低的會少補）；
  // 只要有一項讀不到就不顯示，用 0 會把店家收益高估。
  const feeRates = [settings.data?.linepay_fee_pct, settings.data?.taiwanpay_fee_pct]
    .map((rate) => (rate == null || String(rate).trim() === "" ? Number.NaN : Number(rate)))
    .filter((rate) => Number.isFinite(rate) && rate >= 0 && rate < 1);
  const feeRate = !settings.isError && feeRates.length === 2 ? Math.max(...feeRates) : null;
  const feeRateForPricing = feeRates.length > 0 ? Math.max(...feeRates) : 0;
  return {
    taxRate,
    feeRate,
    feeRateForPricing,
    defaultMargin: settings.data?.purchase_default_margin_pct,
  };
}

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

// -- 建立品項 --
function CreateMenuItemForm({ onCreated, rates }: { onCreated: () => void; rates: PricingRates }) {
  const [name, setName] = useState("");
  // null = 店員還沒自己改過售價 → 顯示建議售價；改過就以他填的為準，不再被自動覆蓋
  //（否則他打完價格、回頭調一下毛利率，剛打的數字就沒了）。
  const [price, setPrice] = useState<string | null>(null);
  const [cost, setCost] = useState("");
  const [margin, setMargin] = useState<string | null>(null);
  const [category, setCategory] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const { taxRate, feeRateForPricing, defaultMargin } = rates;
  // 毛利率顯示值：沒碰過就用設定的預設（設定還沒載入時留白，不要先塞 0 再跳動）。
  const marginValue = margin ?? (defaultMargin === undefined ? "" : String(defaultMargin));
  const marginNum = /^\d+$/.test(marginValue.trim()) ? Number(marginValue) : Number.NaN;
  const marginValid = Number.isInteger(marginNum) && marginNum >= 0 && marginNum <= 99;
  const costNum = parseNtd(cost);
  const suggested =
    taxRate !== null && costNum !== null && costNum > 0 && marginValid
      ? suggestedListedPrice(costNum, marginNum, taxRate, feeRateForPricing)
      : null;
  const priceValue = price ?? (suggested === null ? "" : String(suggested));

  const create = useMutation({
    mutationFn: async () => {
      const p = parseNtd(priceValue);
      if (!name.trim()) throw new Error("請輸入品名");
      if (p === null || p <= 0) throw new Error("售價須為正整數元");
      // 成本留白＝**未知**，送 null；填 0 會讓報表以為毛利 100%。
      if (cost.trim() !== "" && (costNum === null || costNum < 0)) {
        throw new Error("成本須為 0 以上的整數元");
      }
      const { data, error } = await api.POST("/api/v1/menu-items", {
        body: {
          name: name.trim(),
          unit_price: String(p),
          unit_cost: cost.trim() === "" ? null : String(costNum),
          category: category.trim() || null,
          sort_order: 0,
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "建立品項失敗");
      return data;
    },
    onSuccess: () => {
      setFormError(null);
      setName("");
      setPrice(null);
      setCost("");
      setMargin(null);
      setCategory("");
      onCreated();
    },
    onError: (err: Error) => setFormError(err.message),
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    create.mutate();
  }

  return (
    <form className="card menu-form" onSubmit={handleSubmit}>
      <h2>新增品項</h2>
      <div className="menu-form-grid">
        <label className="field">
          <span className="field-label">品名</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：手沖-耶加雪菲"
            required
          />
        </label>
        <label className="field">
          <span className="field-label">成本（整數元，選填）</span>
          <input
            inputMode="numeric"
            value={cost}
            onChange={(e) => setCost(e.target.value)}
            placeholder="60"
          />
        </label>
        <label className="field">
          <span className="field-label">預估毛利率（%）</span>
          <input
            inputMode="numeric"
            value={marginValue}
            onChange={(e) => setMargin(e.target.value)}
            placeholder="30"
          />
        </label>
        <label className="field">
          <span className="field-label">售價（整數元）</span>
          <input
            inputMode="numeric"
            value={priceValue}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="180"
            required
          />
        </label>
        <label className="field">
          <span className="field-label">分類（選填）</span>
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="例如：咖啡"
          />
        </label>
      </div>
      <p className="hint">
        成本含材料與包材（濾掛的掛耳袋、外帶杯蓋等一併算進去）；留白代表成本未知，報表不會替它算毛利。
      </p>
      {marginValue.trim() !== "" && !marginValid && (
        <p role="alert" className="form-error">
          毛利率請輸入 0–99 的整數
        </p>
      )}
      {costNum !== null && costNum > 0 && marginValid && suggested === null && (
        <p role="status" className="hint">
          讀不到稅率設定，暫不計算建議售價，請直接輸入售價。
        </p>
      )}
      {suggested !== null && (
        <p className="hint">
          建議售價 <strong className="money">{formatNtd(suggested)}</strong>
          （成本 {formatNtd(costNum ?? 0)}・毛利 {marginNum}%・已含營業稅與行動支付手續費）。
          可直接改售價，改了就以你填的為準。
        </p>
      )}
      {formError !== null && (
        <p role="alert" className="form-error">
          {formError}
        </p>
      )}
      <button type="submit" className="btn-primary" disabled={create.isPending}>
        {create.isPending ? "建立中…" : "新增品項"}
      </button>
    </form>
  );
}

// -- 單列操作（改價/上下架/封存）--
function MenuItemRow({
  item,
  onChanged,
  rates,
}: {
  item: MenuItemRead;
  onChanged: () => void;
  rates: PricingRates;
}) {
  const [editing, setEditing] = useState(false);
  const [price, setPrice] = useState(item.unit_price);
  const [editingCost, setEditingCost] = useState(false);
  const [cost, setCost] = useState(item.unit_cost ?? "");
  const [rowError, setRowError] = useState<string | null>(null);

  const patch = useMutation({
    mutationFn: async (body: components["schemas"]["MenuItemUpdateRequest"]) => {
      const { data, error } = await api.PATCH("/api/v1/menu-items/{item_id}", {
        params: { path: { item_id: item.id } },
        body,
      });
      if (!data) throw new Error(extractDetail(error) ?? "更新失敗");
      return data;
    },
    onSuccess: () => {
      setRowError(null);
      setEditing(false);
      setEditingCost(false);
      onChanged();
    },
    onError: (err: Error) => setRowError(err.message),
  });

  const archive = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.DELETE("/api/v1/menu-items/{item_id}", {
        params: { path: { item_id: item.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "刪除失敗");
      return data;
    },
    onSuccess: () => {
      setRowError(null);
      onChanged();
    },
    onError: (err: Error) => setRowError(err.message),
  });

  function savePrice() {
    const p = parseNtd(price);
    if (p === null || p <= 0) {
      setRowError("售價須為正整數元");
      return;
    }
    patch.mutate({ unit_price: String(p) });
  }

  function saveCost() {
    // 清空＝把成本改回「未知」（送 null）。填 0 會讓報表以為毛利 100%，兩者不可混為一談。
    if (cost.trim() === "") {
      patch.mutate({ unit_cost: null });
      return;
    }
    const c = parseNtd(cost);
    if (c === null || c < 0) {
      setRowError("成本須為 0 以上的整數元");
      return;
    }
    patch.mutate({ unit_cost: String(c) });
  }

  const costNum = item.unit_cost == null ? null : parseNtd(item.unit_cost);
  const priceNum = parseNtd(item.unit_price);
  const margin =
    costNum !== null && priceNum !== null && rates.taxRate !== null && rates.feeRate !== null
      ? marginPct(priceNum, costNum, rates.taxRate, rates.feeRate)
      : null;

  return (
    <tr>
      <td>{item.name}</td>
      <td>{item.category ?? "—"}</td>
      <td>
        {editing ? (
          <span className="menu-edit-price">
            <input
              className="pos-qty"
              inputMode="numeric"
              value={price}
              aria-label={`${item.name} 售價`}
              onChange={(e) => setPrice(e.target.value)}
            />
            <button type="button" className="btn-ghost" onClick={savePrice}>
              儲存
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setEditing(false);
                setPrice(item.unit_price);
                setRowError(null);
              }}
            >
              取消
            </button>
          </span>
        ) : (
          <span className="menu-price-cell">
            <span className="money">{formatNtd(parseNtd(item.unit_price) ?? 0)}</span>
            <button type="button" className="btn-ghost" onClick={() => setEditing(true)}>
              改價
            </button>
          </span>
        )}
      </td>
      <td>
        {editingCost ? (
          <span className="menu-edit-price">
            <input
              className="pos-qty"
              inputMode="numeric"
              value={cost}
              aria-label={`${item.name} 成本`}
              onChange={(e) => setCost(e.target.value)}
            />
            <button type="button" className="btn-ghost" onClick={saveCost}>
              儲存
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setEditingCost(false);
                setCost(item.unit_cost ?? "");
                setRowError(null);
              }}
            >
              取消
            </button>
          </span>
        ) : (
          <span className="menu-price-cell">
            {costNum === null ? (
              <span className="hint">未填</span>
            ) : (
              <span className="money">{formatNtd(costNum)}</span>
            )}
            <button type="button" className="btn-ghost" onClick={() => setEditingCost(true)}>
              改成本
            </button>
          </span>
        )}
      </td>
      <td>{margin === null ? "—" : `${margin}%`}</td>
      <td>
        <span className={`inv-badge inv-tone-${item.is_available ? "ok" : "muted"}`}>
          {item.is_available ? "可售" : "停售"}
        </span>
      </td>
      <td>
        <div className="menu-row-actions">
          <button
            type="button"
            className="btn-ghost"
            disabled={patch.isPending}
            onClick={() => patch.mutate({ is_available: !item.is_available })}
          >
            {item.is_available ? "下架" : "上架"}
          </button>
          <button
            type="button"
            className="btn-ghost btn-danger-text"
            disabled={archive.isPending}
            onClick={() => archive.mutate()}
          >
            刪除
          </button>
        </div>
        {rowError !== null && (
          <p role="alert" className="form-error menu-row-error">
            {rowError}
          </p>
        )}
      </td>
    </tr>
  );
}

export default function MenuPage() {
  const queryClient = useQueryClient();
  // DB 現值角色（升權未重登也生效；與導覽同源，Codex 波次三第二輪）
  const { isManager } = useCurrentRole();
  const rates = usePricingRates(isManager);

  const listQuery = useQuery({
    queryKey: ["menu-items", "manage"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/menu-items");
      if (!data) throw new Error(extractDetail(error) ?? "讀取菜單失敗");
      return data;
    },
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["menu-items"] });
  }

  if (!isManager) {
    return (
      <section>
        <h1 className="page-title">餐飲菜單</h1>
        <p>需管理者權限</p>
      </section>
    );
  }

  return (
    <section>
      <h1 className="page-title">餐飲菜單</h1>
      <CreateMenuItemForm onCreated={refresh} rates={rates} />

      <div className="menu-list-section">
        {listQuery.isError && (
          <p role="alert" className="form-error">
            {listQuery.error.message}
          </p>
        )}
        <div className="inv-table-wrap">
          <table className="inv-table">
            <thead>
              <tr>
                <th>品名</th>
                <th>分類</th>
                <th>售價</th>
                <th>成本</th>
                <th>預估毛利率</th>
                <th>狀態</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(listQuery.data ?? []).map((item) => (
                <MenuItemRow key={item.id} item={item} onChanged={refresh} rates={rates} />
              ))}
            </tbody>
          </table>
          {(rates.taxRate === null || rates.feeRate === null) && (
            <p role="status" className="hint">
              讀不到稅率或行動支付費率設定，預估毛利率暫不顯示。
            </p>
          )}
          {listQuery.isSuccess && listQuery.data.length === 0 && (
            <p className="hint">尚無餐飲品項</p>
          )}
        </div>
      </div>
    </section>
  );
}
