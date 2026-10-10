"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useDialogFocus } from "@/features/common/useDialogFocus";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type Presentation = components["schemas"]["MenuPresentationRead"];
type Update = components["schemas"]["MenuPresentationUpdateRequest"];
type Role = NonNullable<Update["role"]>;
/** 加購角色（docs/63 §6）：咖啡→推甜點、甜點→推咖啡、手沖體驗→推咖啡豆／濾掛、咖啡豆→推濾掛／其他豆款。 */
const ROLE_OPTIONS: { value: Role | ""; label: string }[] = [
  { value: "", label: "不參與加購" },
  { value: "coffee", label: "咖啡" },
  { value: "dessert", label: "甜點" },
  { value: "experience", label: "手沖體驗" },
  { value: "bean", label: "咖啡豆" },
  { value: "drip", label: "濾掛" },
  { value: "other", label: "其他" },
];
function detail(error: unknown, fallback: string): string {
  return error && typeof error === "object" && "detail" in error && typeof error.detail === "string" ? error.detail : fallback;
}

export function MenuPresentationDialog({ itemId, itemName, onDone, onClose }: {
  itemId: number; itemName: string; onDone: () => void; onClose: () => void;
}) {
  const ref = useDialogFocus<HTMLDivElement>();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const queryKey = ["menu-presentation", itemId];
  const query = useQuery({
    queryKey, refetchOnWindowFocus: false, refetchOnReconnect: false,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/online-order/menu-items/{item_id}/presentation", { params: { path: { item_id: itemId } } });
      if (!data) throw new Error(detail(error, "讀取線上呈現設定失敗"));
      return data;
    },
  });
  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label={`${itemName} 的線上呈現`} ref={ref} tabIndex={-1}
      onKeyDown={(event) => { if (event.key === "Escape" && !busy) onClose(); }}>
      <div className="card menu-presentation-dialog">
        <div className="menu-presentation-heading"><h2>{itemName} · 線上呈現</h2><button type="button" className="btn-ghost" aria-label="關閉" disabled={busy} onClick={onClose}>×</button></div>
        <p className="hint">儲存後請「發佈到線上點餐」，客人就會看到新的介紹與顯示設定。</p>
        {query.isFetching && <p role="status">讀取設定中…</p>}
        {query.isError && <div role="alert"><p className="form-error">{query.error.message}</p><button type="button" className="btn-secondary" onClick={() => void query.refetch()}>重新讀取</button></div>}
        {query.data && !query.isFetching && !query.isError && <PresentationForm initial={query.data} setBusy={setBusy} onSaved={(saved) => { queryClient.setQueryData(queryKey, saved); onDone(); }} />}
      </div>
    </div>
  );
}

function PresentationForm({ initial, setBusy, onSaved }: { initial: Presentation; setBusy: (busy: boolean) => void; onSaved: (saved: Presentation) => void }) {
  const [flavor, setFlavor] = useState(initial.flavor_description ?? "");
  const [audience, setAudience] = useState(initial.audience_description ?? "");
  const [isNew, setNew] = useState(initial.is_new);
  const [limitedOn, setLimitedOn] = useState(initial.limited_on ?? "");
  const [showRemaining, setShowRemaining] = useState(initial.show_remaining);
  const [threshold, setThreshold] = useState(String(initial.low_stock_threshold));
  const [hideSoldOut, setHideSoldOut] = useState(initial.hide_sold_out);
  const [role, setRole] = useState<Role | "">(initial.role ?? "");
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: async (body: Update) => {
      const { data, error } = await api.PUT("/api/v1/online-order/menu-items/{item_id}/presentation", { params: { path: { item_id: initial.menu_item_id } }, body });
      if (!data) throw new Error(detail(error, "儲存線上呈現設定失敗"));
      return data;
    },
    onMutate: () => { setBusy(true); setError(null); },
    onSuccess: onSaved,
    onError: (reason: Error) => setError(reason.message),
    onSettled: () => setBusy(false),
  });
  return (
    <form className="menu-presentation-form" onSubmit={(event) => {
      event.preventDefault();
      const value = Number(threshold);
      if (threshold.trim() === "" || !Number.isInteger(value) || value < 0 || value > 9999) { setError("低庫存門檻請填 0–9999 的整數。"); return; }
      save.mutate({ flavor_description: flavor.trim() || null, audience_description: audience.trim() || null,
        is_new: isNew, limited_on: limitedOn || null,
        show_remaining: showRemaining, low_stock_threshold: value, hide_sold_out: hideSoldOut, role: role || null });
    }}>
      <fieldset disabled={save.isPending}>
        <label className="field"><span className="field-label">風味描述</span><input value={flavor} maxLength={120} placeholder="例如：蜜桃・花香・甜感" onChange={(event) => setFlavor(event.target.value)} /></label>
        <label className="field"><span className="field-label">適合族群</span><input value={audience} maxLength={120} placeholder="例如：適合喜歡果香與明亮酸甜的人" onChange={(event) => setAudience(event.target.value)} /></label>
        <div className="menu-presentation-checks">
          <label><input type="checkbox" checked={isNew} onChange={(event) => setNew(event.target.checked)} />新品 NEW</label>
        </div>
        <label className="field"><span className="field-label">今日限定日期</span><input type="date" value={limitedOn} onChange={(event) => setLimitedOn(event.target.value)} /></label>
        <p className="hint">只在指定的台北日期顯示「今日限定」標籤，清空日期即可取消標籤。</p>
        <label className="field"><span className="field-label">加購角色</span>
          <select value={role} onChange={(event) => setRole(event.target.value as Role | "")}>
            {ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <p className="hint">客人點了咖啡，購物車下方會推「甜點」角色的品項（反之亦然）；最多推 2 項、不會自動加入。</p>
        <h3>庫存顯示</h3>
        <div className="menu-presentation-checks"><label><input type="checkbox" checked={showRemaining} onChange={(event) => setShowRemaining(event.target.checked)} />低庫存時顯示剩餘數量</label></div>
        <label className="field"><span className="field-label">低庫存顯示門檻</span><input type="number" min={0} max={9999} step={1} required value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label>
        <p className="hint">例如填 5，剩 2–5 份時顯示數量，剩 1 份顯示「最後 1 份」。超過門檻不顯示；不限量商品不顯示數量。</p>
        <div className="menu-presentation-checks"><label><input type="checkbox" checked={hideSoldOut} onChange={(event) => setHideSoldOut(event.target.checked)} />售完後完全隱藏</label></div>
        <p className="hint">未勾選時，售完商品會保留在分類後方並標示「今日售完」。</p>
      </fieldset>
      {error && <p role="alert" className="form-error">{error}</p>}
      <div className="pos-dialog-actions"><button type="submit" className="btn-primary" disabled={save.isPending}>{save.isPending ? "儲存中…" : "儲存設定"}</button></div>
    </form>
  );
}
