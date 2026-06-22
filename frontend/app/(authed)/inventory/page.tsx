"use client";
// /inventory 庫存瀏覽（docs/10 §/inventory）：序號品 / 數量品 / 散裝批 三分頁，唯讀清單＋篩選＋
// 搜尋＋分頁，全部走 OpenAPI 生成型別 client（docs/11，禁手刻型別）。
//
// 補印標籤：每列一顆「補印標籤」（IN_STOCK 序號品 / ON_SALE 散裝批），直接複用 hardware-agent
// 的 /print/label——清單列本就帶齊 條碼/品名/售價，免再查 by-code、免改後端。
// 待後端端點（F5b，需核准後補）：改價留痕、上下架、商品照片。本版不放假按鈕（沿 cash 頁慣例）。
import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useState } from "react";

import {
  type Badge,
  bulkStatusBadge,
  gradeLabel,
  isLowStock,
  orUndefined,
  ownershipBadge,
  sellThroughPct,
  serializedStatusBadge,
} from "@/features/inventory/inventory";
import { printLabel } from "@/lib/agent";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type SerializedItem = components["schemas"]["SerializedItemRead"];
type BulkLot = components["schemas"]["BulkLotRead"];
type CatalogProduct = components["schemas"]["CatalogProductRead"];
type SerializedStatus = components["schemas"]["SerializedItemStatus"];
type BulkStatus = components["schemas"]["BulkLotStatus"];
type Ownership = components["schemas"]["OwnershipType"];

type Tab = "serialized" | "catalog" | "bulk";
const PAGE_SIZE = 20;

// 下拉選項（openapi-typescript 只生成型別、不生成 runtime 陣列；以生成型別標註保元素合法）。
const SERIALIZED_STATUSES: SerializedStatus[] = [
  "IN_STOCK",
  "SOLD",
  "RETURNED_TO_CONSIGNOR",
  "WRITTEN_OFF",
];
const OWNERSHIPS: Ownership[] = ["OWNED", "CONSIGNMENT"];
const BULK_STATUSES: BulkStatus[] = ["ON_SALE", "SOLD_OUT", "WRITTEN_OFF"];

const TABS: { key: Tab; label: string }[] = [
  { key: "serialized", label: "序號品" },
  { key: "catalog", label: "數量品" },
  { key: "bulk", label: "散裝批" },
];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function MoneyText({ value }: { value: string | null | undefined }) {
  if (value === null || value === undefined) return <span className="money">—</span>;
  const parsed = parseNtd(value);
  return <span className="money">{parsed === null ? value : formatNtd(parsed)}</span>;
}

function BadgeChip({ badge }: { badge: Badge }) {
  return <span className={`inv-badge inv-tone-${badge.tone}`}>{badge.label}</span>;
}

// 單件補印：條碼/品名/整數元售價直接來自清單列；經 hardware-agent /print/label。
function ReprintLabelButton({
  code,
  name,
  price,
}: {
  code: string;
  name: string;
  price: number;
}) {
  const print = useMutation({ mutationFn: () => printLabel(code, name, price) });
  return (
    <span className="inv-reprint">
      <button
        type="button"
        className="btn-ghost inv-reprint-btn"
        onClick={() => print.mutate()}
        disabled={print.isPending}
      >
        {print.isPending ? "列印中…" : "補印標籤"}
      </button>
      {print.isSuccess && <span className="inv-reprint-ok">✓ 已送出</span>}
      {print.isError && (
        <span className="form-error inv-reprint-err" title={print.error.message}>
          ✗ 失敗
        </span>
      )}
    </span>
  );
}

function Pagination({
  page,
  count,
  onPage,
}: {
  page: number;
  count: number;
  onPage: (next: number) => void;
}) {
  // 無總筆數端點：滿頁（count===PAGE_SIZE）視為「可能有下一頁」。
  const hasNext = count === PAGE_SIZE;
  return (
    <div className="inv-pager">
      <button
        type="button"
        className="btn-ghost"
        disabled={page === 0}
        onClick={() => onPage(page - 1)}
      >
        上一頁
      </button>
      <span className="hint">第 {page + 1} 頁</span>
      <button
        type="button"
        className="btn-ghost"
        disabled={!hasNext}
        onClick={() => onPage(page + 1)}
      >
        下一頁
      </button>
    </div>
  );
}

function TableShell({
  loading,
  error,
  empty,
  headers,
  children,
}: {
  loading: boolean;
  error: string | null;
  empty: boolean;
  headers: string[];
  children: ReactNode;
}) {
  if (error !== null)
    return (
      <p role="alert" className="form-error">
        {error}
      </p>
    );
  return (
    <div className="inv-table-wrap">
      <table className="inv-table">
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
      {loading && <p className="hint">載入中…</p>}
      {!loading && empty && <p className="hint inv-empty">查無資料</p>}
    </div>
  );
}

function SearchBar({
  placeholder,
  onSearch,
  children,
}: {
  placeholder: string;
  onSearch: (q: string) => void;
  children?: ReactNode;
}) {
  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const q = String(new FormData(event.currentTarget).get("q") ?? "");
    onSearch(q.trim());
  }
  return (
    <form className="inv-filters" onSubmit={onSubmit}>
      {children}
      <input name="q" placeholder={placeholder} className="inv-search" aria-label="搜尋" />
      <button type="submit" className="btn-primary">
        查詢
      </button>
    </form>
  );
}

function SerializedPanel() {
  const [status, setStatus] = useState<SerializedStatus | "">("");
  const [ownership, setOwnership] = useState<Ownership | "">("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);

  const query = useQuery({
    queryKey: ["inventory", "serialized", { status, ownership, q, page }],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/serialized-items", {
        params: {
          query: {
            status: orUndefined(status),
            ownership: orUndefined(ownership),
            q: orUndefined(q),
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          },
        },
      });
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取序號品失敗");
    },
  });
  const rows: SerializedItem[] = query.data ?? [];

  return (
    <div className="inv-panel">
      <SearchBar placeholder="品名 / 序號碼" onSearch={(value) => { setQ(value); setPage(0); }}>
        <select
          aria-label="狀態"
          value={status}
          onChange={(e) => { setStatus(e.target.value as SerializedStatus | ""); setPage(0); }}
        >
          <option value="">全部狀態</option>
          {SERIALIZED_STATUSES.map((s) => (
            <option key={s} value={s}>
              {serializedStatusBadge(s).label}
            </option>
          ))}
        </select>
        <select
          aria-label="持有方式"
          value={ownership}
          onChange={(e) => { setOwnership(e.target.value as Ownership | ""); setPage(0); }}
        >
          <option value="">全部持有</option>
          {OWNERSHIPS.map((o) => (
            <option key={o} value={o}>
              {ownershipBadge(o).label}
            </option>
          ))}
        </select>
      </SearchBar>
      <TableShell
        loading={query.isFetching}
        error={query.isError ? query.error.message : null}
        empty={rows.length === 0}
        headers={["序號碼", "品名", "成色", "持有", "狀態", "標價", "標籤"]}
      >
        {rows.map((item) => (
          <tr key={item.id}>
            <td className="inv-code">{item.item_code}</td>
            <td>{item.name}</td>
            <td>{gradeLabel(item.grade)}</td>
            <td>
              <BadgeChip badge={ownershipBadge(item.ownership_type)} />
            </td>
            <td>
              <BadgeChip badge={serializedStatusBadge(item.status)} />
            </td>
            <td>
              <MoneyText value={item.listed_price} />
            </td>
            <td>
              {item.status === "IN_STOCK" && (
                <ReprintLabelButton
                  code={item.item_code}
                  name={item.name}
                  price={parseNtd(item.listed_price) ?? 0}
                />
              )}
            </td>
          </tr>
        ))}
      </TableShell>
      <Pagination page={page} count={rows.length} onPage={setPage} />
    </div>
  );
}

function CatalogPanel() {
  const [lowStock, setLowStock] = useState(false);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);

  const query = useQuery({
    queryKey: ["inventory", "catalog", { lowStock, q, page }],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/catalog-products", {
        params: {
          query: {
            q: orUndefined(q),
            low_stock: lowStock ? true : undefined,
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          },
        },
      });
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取數量品失敗");
    },
  });
  const rows: CatalogProduct[] = query.data ?? [];

  return (
    <div className="inv-panel">
      <SearchBar placeholder="品名 / SKU" onSearch={(value) => { setQ(value); setPage(0); }}>
        <label className="inv-check">
          <input
            type="checkbox"
            checked={lowStock}
            onChange={(e) => { setLowStock(e.target.checked); setPage(0); }}
          />
          僅顯示低庫存
        </label>
      </SearchBar>
      <TableShell
        loading={query.isFetching}
        error={query.isError ? query.error.message : null}
        empty={rows.length === 0}
        headers={["SKU", "品名", "單價", "現有量", "再訂購點", ""]}
      >
        {rows.map((product) => {
          const low = isLowStock(product.quantity_on_hand, product.reorder_point);
          return (
            <tr key={product.id}>
              <td className="inv-code">{product.sku}</td>
              <td>{product.name}</td>
              <td>
                <MoneyText value={product.unit_price} />
              </td>
              <td>{product.quantity_on_hand}</td>
              <td>{product.reorder_point}</td>
              <td>{low && <BadgeChip badge={{ label: "低庫存", tone: "warn" }} />}</td>
            </tr>
          );
        })}
      </TableShell>
      <Pagination page={page} count={rows.length} onPage={setPage} />
    </div>
  );
}

function BulkPanel() {
  const [status, setStatus] = useState<BulkStatus | "">("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);

  const query = useQuery({
    queryKey: ["inventory", "bulk", { status, q, page }],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/bulk-lots", {
        params: {
          query: {
            status: orUndefined(status),
            q: orUndefined(q),
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          },
        },
      });
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取散裝批失敗");
    },
  });
  const rows: BulkLot[] = query.data ?? [];

  return (
    <div className="inv-panel">
      <SearchBar placeholder="名稱 / 批號" onSearch={(value) => { setQ(value); setPage(0); }}>
        <select
          aria-label="狀態"
          value={status}
          onChange={(e) => { setStatus(e.target.value as BulkStatus | ""); setPage(0); }}
        >
          <option value="">全部狀態</option>
          {BULK_STATUSES.map((s) => (
            <option key={s} value={s}>
              {bulkStatusBadge(s).label}
            </option>
          ))}
        </select>
      </SearchBar>
      <TableShell
        loading={query.isFetching}
        error={query.isError ? query.error.message : null}
        empty={rows.length === 0}
        headers={["批號", "名稱", "成色", "均一價", "剩餘/總", "收購成本", "售出進度", "狀態", "標籤"]}
      >
        {rows.map((lot) => (
          <tr key={lot.id}>
            <td className="inv-code">{lot.lot_code}</td>
            <td>{lot.name}</td>
            <td>{gradeLabel(lot.grade)}</td>
            <td>
              <MoneyText value={lot.unit_price} />
            </td>
            <td>
              {lot.remaining_qty} / {lot.total_qty}
            </td>
            <td>
              <MoneyText value={lot.acquisition_cost} />
            </td>
            <td>{sellThroughPct(lot.total_qty, lot.remaining_qty)}%</td>
            <td>
              <BadgeChip badge={bulkStatusBadge(lot.status)} />
            </td>
            <td>
              {lot.status === "ON_SALE" && lot.remaining_qty > 0 && (
                <ReprintLabelButton
                  code={lot.lot_code}
                  name={lot.name}
                  price={parseNtd(lot.unit_price) ?? 0}
                />
              )}
            </td>
          </tr>
        ))}
      </TableShell>
      <Pagination page={page} count={rows.length} onPage={setPage} />
    </div>
  );
}

export default function InventoryPage() {
  const [tab, setTab] = useState<Tab>("serialized");
  return (
    <section>
      <h1 className="page-title">庫存</h1>
      <div className="inv-tabs" role="tablist">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "inv-tab inv-tab-active" : "inv-tab"}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "serialized" && <SerializedPanel />}
      {tab === "catalog" && <CatalogPanel />}
      {tab === "bulk" && <BulkPanel />}
    </section>
  );
}
