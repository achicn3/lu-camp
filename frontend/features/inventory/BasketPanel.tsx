"use client";

// 庫存頁「販售籃」分頁（ADR-025）：同樣的散裝分次收購，放同一籃、貼同一張標籤、賣同一個價。
// 一籃一列看總剩餘；展開看每次收購的來源（數量、成本各自保留，不合併）。
// 改籃價限管理者，改完要重印籃子標籤——價格印在標籤上，不重印就會跟 POS 對不起來。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, Fragment, useMemo, useState } from "react";

import { printLabel } from "@/lib/agent";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import { formatTaipeiDate } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type BulkBasket = components["schemas"]["BulkBasketRead"];

const STATUS_LABEL: Record<components["schemas"]["BulkLotStatus"], string> = {
  PENDING_LISTING: "待整理",
  ON_SALE: "販售中",
  SOLD_OUT: "售完",
  WRITTEN_OFF: "收購已作廢",
};

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const value = (error as { detail: unknown }).detail;
    if (typeof value === "string") return value;
  }
  return null;
}

function money(value: string | null | undefined): string {
  return formatNtd(parseNtd(value ?? "0") ?? 0);
}

function costRange(basket: BulkBasket): string {
  const { unit_cost_min: min, unit_cost_max: max } = basket.cost_reference;
  if (min == null || max == null) return "—";
  return min === max ? money(min) : `${money(min)}–${money(max)}`;
}

export function BasketPanel() {
  const isManager = useMemo(() => decodeSession()?.role === "MANAGER", []);
  const [q, setQ] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const baskets = useQuery({
    queryKey: ["bulk-baskets", "inventory", q, showInactive],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/bulk-baskets", {
        params: { query: { q: q || undefined, include_inactive: showInactive } },
      });
      if (!data) throw new Error(detail(error) ?? "讀取販售籃失敗");
      return data;
    },
  });
  // 標籤要印品牌名；籃子只存 id，另查一次品牌清單對照。
  const brands = useQuery({
    queryKey: ["brands", "basket-labels"],
    queryFn: async () =>
      (await api.GET("/api/v1/brands", { params: { query: { limit: 200 } } })).data ?? [],
  });

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQ(String(new FormData(event.currentTarget).get("q") ?? "").trim());
  }

  const rows = baskets.data ?? [];
  return (
    <div>
      <p className="hint">
        同樣的散裝（例如無品牌營釘）分次收進來，可以放同一籃、共用一張標籤。收購時在散裝分頁選「開新販售籃」或「加入現有販售籃」。
      </p>
      <form className="inv-filters" onSubmit={onSearch}>
        <input name="q" placeholder="搜尋販售籃名稱" className="inv-search" aria-label="搜尋販售籃" />
        <button type="submit" className="btn-primary">
          查詢
        </button>
        <label className="inv-check">
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(e) => setShowInactive(e.target.checked)}
          />
          含不再加入收購的
        </label>
      </form>
      {notice !== null && (
        <p role="status" className="form-success">
          {notice}
        </p>
      )}
      {baskets.isError && (
        <p role="alert" className="form-error">
          {baskets.error.message}
        </p>
      )}
      <div className="inv-table-wrap">
        <table className="inv-table">
          <thead>
            <tr>
              <th>名稱</th>
              <th>標籤條碼</th>
              <th>每件售價</th>
              <th>剩餘件數</th>
              <th>單件收購成本</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((basket) => (
              <Fragment key={basket.id}>
                <BasketRow
                  basket={basket}
                  brandName={
                    basket.brand_id === null
                      ? null
                      : brands.data?.find((b) => b.id === basket.brand_id)?.name
                  }
                  isManager={isManager}
                  expanded={expanded === basket.id}
                  onToggle={() => setExpanded(expanded === basket.id ? null : basket.id)}
                  onNotice={setNotice}
                />
                {expanded === basket.id && <SourcesRow basket={basket} />}
              </Fragment>
            ))}
          </tbody>
        </table>
        {baskets.isLoading && <p className="hint">載入中…</p>}
        {!baskets.isLoading && rows.length === 0 && (
          <p className="hint inv-empty">還沒有販售籃</p>
        )}
      </div>
    </div>
  );
}

function BasketRow({
  basket,
  brandName,
  isManager,
  expanded,
  onToggle,
  onNotice,
}: {
  basket: BulkBasket;
  /** undefined＝品牌名稱還沒查到（不送印，免得印成沒有品牌）。 */
  brandName: string | null | undefined;
  isManager: boolean;
  expanded: boolean;
  onToggle: () => void;
  onNotice: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [price, setPrice] = useState(String(parseNtd(basket.unit_price) ?? ""));

  const save = useMutation({
    mutationFn: async (changes: { unit_price?: string; is_active?: boolean }) => {
      const { data, error } = await api.PATCH("/api/v1/bulk-baskets/{basket_id}", {
        params: { path: { basket_id: basket.id } },
        body: changes,
      });
      if (!data) throw new Error(detail(error) ?? "儲存失敗");
      return data;
    },
    onSuccess: (data, changes) => {
      setEditing(false);
      void queryClient.invalidateQueries({ queryKey: ["bulk-baskets"] });
      onNotice(
        changes.unit_price !== undefined
          ? `「${data.name}」已改為每件 ${money(data.unit_price)} 元，請補印籃子標籤貼上。`
          : changes.is_active
            ? `「${data.name}」已恢復，收購時可以再選它。`
            : `「${data.name}」不再加入新的收購；剩下的貨照常可以賣，舊標籤不用撕。`,
      );
    },
  });

  const print = useMutation({
    mutationFn: () => {
      if (brandName === undefined) throw new Error("品牌名稱尚未取得，請重新整理後再試");
      return printLabel(basket.code, basket.name, parseNtd(basket.unit_price) ?? 0, {
        brand: brandName,
        condition: "二手",
      });
    },
  });

  function submitPrice() {
    const value = parseNtd(price);
    if (value === null || value <= 0) return;
    save.mutate({ unit_price: String(value) });
  }

  return (
    <tr className={basket.is_active ? undefined : "inv-row-muted"}>
      <td>
        {basket.name}
        {!basket.is_active && <span className="hint">（不再加入收購）</span>}
      </td>
      <td className="mono">{basket.code}</td>
      <td className="money">
        {editing ? (
          <input
            aria-label={`${basket.name} 新售價`}
            inputMode="numeric"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            className="inv-price-input"
          />
        ) : (
          money(basket.unit_price)
        )}
      </td>
      <td>{basket.remaining_qty}</td>
      <td className="money">{costRange(basket)}</td>
      <td className="inv-actions">
        <button type="button" className="btn-ghost" onClick={onToggle} aria-expanded={expanded}>
          來源（{basket.sources.length} 批）
        </button>
        {isManager && !editing && (
          <button type="button" className="btn-ghost" onClick={() => setEditing(true)}>
            改售價
          </button>
        )}
        {isManager && editing && (
          <>
            <button
              type="button"
              className="btn-primary"
              onClick={submitPrice}
              disabled={save.isPending}
            >
              儲存售價
            </button>
            <button type="button" className="btn-ghost" onClick={() => setEditing(false)}>
              取消
            </button>
          </>
        )}
        {isManager && (
          // 只擋新的收購加入，不擋販售（架上的貨還是要賣完）；所以不叫「停用」。
          <button
            type="button"
            className="btn-ghost"
            onClick={() => save.mutate({ is_active: !basket.is_active })}
            disabled={save.isPending}
            title="收購時不再列出這一籃；剩下的貨照常可以賣"
          >
            {basket.is_active ? "停止加入收購" : "恢復加入收購"}
          </button>
        )}
        <button
          type="button"
          className="btn-ghost"
          onClick={() => print.mutate()}
          disabled={print.isPending || brandName === undefined}
        >
          {print.isPending ? "列印中…" : "印籃子標籤"}
        </button>
        {print.isSuccess && <span className="inv-reprint-ok">✓ 已送出</span>}
        {print.isError && (
          <span className="form-error" title={print.error.message}>
            ✗ 列印失敗
          </span>
        )}
        {save.isError && <span className="form-error">{save.error.message}</span>}
      </td>
    </tr>
  );
}

function SourcesRow({ basket }: { basket: BulkBasket }) {
  return (
    <tr className="inv-basket-sources">
      <td colSpan={6}>
        {basket.sources.length === 0 ? (
          <p className="hint">這一籃還沒有收購紀錄。</p>
        ) : (
          <table className="inv-table inv-subtable">
            <thead>
              <tr>
                <th>收購日期</th>
                <th>散裝編號</th>
                <th>原數量</th>
                <th>剩餘</th>
                <th>整批成本</th>
                <th>單件成本</th>
                <th>狀態</th>
              </tr>
            </thead>
            <tbody>
              {basket.sources.map((source) => (
                <tr key={source.bulk_lot_id}>
                  <td>{formatTaipeiDate(source.intake_date)}</td>
                  <td className="mono">{source.lot_code}</td>
                  <td>{source.total_qty}</td>
                  <td>{source.remaining_qty}</td>
                  <td className="money">{money(source.acquisition_cost)}</td>
                  <td className="money">{money(source.unit_cost)}</td>
                  <td>{STATUS_LABEL[source.status]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="hint">結帳時先賣最早收進來的那批；退貨會退回原本扣的那批。</p>
      </td>
    </tr>
  );
}
