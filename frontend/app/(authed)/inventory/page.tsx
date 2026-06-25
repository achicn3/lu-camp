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
type SerializedDetail = components["schemas"]["SerializedItemDetailRead"];
type BulkLot = components["schemas"]["BulkLotRead"];
type CatalogProduct = components["schemas"]["CatalogProductRead"];
type SerializedStatus = components["schemas"]["SerializedItemStatus"];
type BulkStatus = components["schemas"]["BulkLotStatus"];
type Ownership = components["schemas"]["OwnershipType"];

type Tab = "serialized" | "aging" | "catalog" | "bulk";
const PAGE_SIZE = 20;
const AGE_PRESETS = [30, 60, 90, 180];

function daysInStock(intakeDate: string): number {
  const ms = Date.now() - new Date(intakeDate).getTime();
  return Math.max(0, Math.floor(ms / 86_400_000));
}

function dt(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString("zh-TW") : "—";
}

// 篩選用品牌/類型選項（單店量小，一次載入；供下拉與明細名稱對照共用）。
function useFilterOptions() {
  const brands = useQuery({
    queryKey: ["brands", "all"],
    queryFn: async () => (await api.GET("/api/v1/brands", { params: { query: { limit: 200 } } })).data ?? [],
  });
  const categories = useQuery({
    queryKey: ["categories", "all"],
    queryFn: async () =>
      (await api.GET("/api/v1/categories", { params: { query: { limit: 200 } } })).data ?? [],
  });
  return { brands: brands.data ?? [], categories: categories.data ?? [] };
}

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
  { key: "aging", label: "久滯庫存" },
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

// 逐件「詳細」：來源（賣方/寄售人）、收購成本/時間、標價/成交價、入庫時間、完整異動歷史。
function ItemDetailModal({
  itemId,
  brandName,
  categoryName,
  onClose,
}: {
  itemId: number;
  brandName: (id: number | null) => string;
  categoryName: (id: number | null) => string;
  onClose: () => void;
}) {
  const detail = useQuery({
    queryKey: ["serialized-detail", itemId],
    queryFn: async (): Promise<SerializedDetail> => {
      const { data, error } = await api.GET("/api/v1/serialized-items/{item_id}/detail", {
        params: { path: { item_id: itemId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取明細失敗");
      return data;
    },
  });

  const d = detail.data;
  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="商品明細">
      <div className="card pos-dialog inv-detail">
        <div className="inv-detail-head">
          <h2>商品明細</h2>
          <button type="button" className="btn-ghost" onClick={onClose}>
            關閉
          </button>
        </div>
        {detail.isPending ? (
          <p>載入中…</p>
        ) : detail.isError ? (
          <p role="alert" className="form-error">
            {detail.error.message}
          </p>
        ) : d ? (
          <>
            <h3 className="inv-detail-name">
              {d.name} <span className="inv-code">{d.item_code}</span>
            </h3>
            <dl className="inv-detail-grid">
              <div>
                <dt>來源</dt>
                <dd>
                  {d.source
                    ? `${d.source.kind === "CONSIGNOR" ? "寄售人" : "賣方"}：${d.source.name ?? "—"}${d.source.phone ? `（${d.source.phone}）` : ""}`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>品牌 / 類型</dt>
                <dd>
                  {brandName(d.brand_id)} / {categoryName(d.category_id)}
                </dd>
              </div>
              <div>
                <dt>成色 / 持有</dt>
                <dd>
                  {gradeLabel(d.grade)}・{ownershipBadge(d.ownership_type).label}
                </dd>
              </div>
              <div>
                <dt>狀態</dt>
                <dd>
                  <BadgeChip badge={serializedStatusBadge(d.status)} />
                </dd>
              </div>
              <div>
                <dt>收購成本</dt>
                <dd>
                  <MoneyText value={d.acquisition_cost} />
                  {d.commission_pct !== null && `（寄售抽成 ${d.commission_pct}%）`}
                </dd>
              </div>
              <div>
                <dt>標價</dt>
                <dd>
                  <MoneyText value={d.listed_price} />
                </dd>
              </div>
              <div>
                <dt>成交價</dt>
                <dd>
                  <MoneyText value={d.sold_price} />
                  {d.sale_id !== null && <span className="row-sub">單號 #{d.sale_id}</span>}
                </dd>
              </div>
              <div>
                <dt>毛利</dt>
                <dd>
                  <MoneyText value={d.margin} />
                </dd>
              </div>
              <div>
                <dt>入庫時間</dt>
                <dd>
                  {dt(d.intake_date)}
                  <span className="row-sub">已在庫 {daysInStock(d.intake_date)} 天</span>
                </dd>
              </div>
              <div>
                <dt>售出時間</dt>
                <dd>{dt(d.sold_date)}</dd>
              </div>
            </dl>
            <h4 className="inv-detail-subtitle">歷史紀錄</h4>
            {d.history.length === 0 ? (
              <p className="hint">尚無異動紀錄。</p>
            ) : (
              <ul className="inv-history">
                {d.history.map((h, i) => (
                  <li key={i}>
                    <span className="inv-history-event">{h.event}</span>
                    <span className="inv-history-at">{dt(h.at)}</span>
                    {h.note && <span className="row-sub">{h.note}</span>}
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}

type CatalogDetail = components["schemas"]["CatalogProductDetailRead"];
type BulkDetail = components["schemas"]["BulkLotDetailRead"];

const PO_STATUS_TEXT: Record<string, string> = {
  DRAFT: "草稿",
  ORDERED: "已下單",
  RECEIVED: "已收貨",
  CLOSED: "已結案",
};

function HistoryList({ history }: { history: { at: string; event: string; note?: string | null }[] }) {
  if (history.length === 0) return <p className="hint">尚無異動紀錄。</p>;
  return (
    <ul className="inv-history">
      {history.map((h, i) => (
        <li key={i}>
          <span className="inv-history-event">{h.event}</span>
          <span className="inv-history-at">{dt(h.at)}</span>
          {h.note && <span className="row-sub">{h.note}</span>}
        </li>
      ))}
    </ul>
  );
}

// 數量品「詳細」（#2 經銷商）：售價/現量＋經銷商進貨歷史＋異動歷史。
function CatalogDetailModal({ productId, onClose }: { productId: number; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ["catalog-detail", productId],
    queryFn: async (): Promise<CatalogDetail> => {
      const { data, error } = await api.GET("/api/v1/catalog-products/{product_id}/detail", {
        params: { path: { product_id: productId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取明細失敗");
      return data;
    },
  });
  const d = detail.data;
  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="數量品明細">
      <div className="card pos-dialog inv-detail">
        <div className="inv-detail-head">
          <h2>數量品明細</h2>
          <button type="button" className="btn-ghost" onClick={onClose}>
            關閉
          </button>
        </div>
        {detail.isPending ? (
          <p>載入中…</p>
        ) : detail.isError ? (
          <p role="alert" className="form-error">
            {detail.error.message}
          </p>
        ) : d ? (
          <>
            <h3 className="inv-detail-name">
              {d.name} <span className="inv-code">{d.sku}</span>
            </h3>
            <dl className="inv-detail-grid">
              <div>
                <dt>售價</dt>
                <dd>
                  <MoneyText value={d.unit_price} />
                </dd>
              </div>
              <div>
                <dt>現有量 / 補貨點</dt>
                <dd>
                  {d.quantity_on_hand} / {d.reorder_point}
                </dd>
              </div>
            </dl>
            <h4 className="inv-detail-subtitle">經銷商進貨歷史</h4>
            {d.purchases.length === 0 ? (
              <p className="hint">尚無進貨紀錄。</p>
            ) : (
              <table className="data-table inv-detail-table">
                <thead>
                  <tr>
                    <th>供應商</th>
                    <th>數量</th>
                    <th>進貨單價</th>
                    <th>狀態</th>
                    <th>下單</th>
                    <th>收貨</th>
                  </tr>
                </thead>
                <tbody>
                  {d.purchases.map((p) => (
                    <tr key={p.po_id}>
                      <td>{p.supplier_name}</td>
                      <td>{p.qty}</td>
                      <td>
                        <MoneyText value={p.unit_cost} />
                      </td>
                      <td>{PO_STATUS_TEXT[p.status] ?? p.status}</td>
                      <td>{dt(p.ordered_at)}</td>
                      <td>{dt(p.received_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <h4 className="inv-detail-subtitle">庫存異動歷史</h4>
            <HistoryList history={d.history} />
          </>
        ) : null}
      </div>
    </div>
  );
}

// 散裝批「詳細」（#2）：來源（賣方/寄售人）、收購成本、均一價、剩餘、入庫時間、異動歷史。
function BulkDetailModal({
  lotId,
  brandName,
  onClose,
}: {
  lotId: number;
  brandName: (id: number | null) => string;
  onClose: () => void;
}) {
  const detail = useQuery({
    queryKey: ["bulk-detail", lotId],
    queryFn: async (): Promise<BulkDetail> => {
      const { data, error } = await api.GET("/api/v1/bulk-lots/{lot_id}/detail", {
        params: { path: { lot_id: lotId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取明細失敗");
      return data;
    },
  });
  const d = detail.data;
  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="散裝批明細">
      <div className="card pos-dialog inv-detail">
        <div className="inv-detail-head">
          <h2>散裝批明細</h2>
          <button type="button" className="btn-ghost" onClick={onClose}>
            關閉
          </button>
        </div>
        {detail.isPending ? (
          <p>載入中…</p>
        ) : detail.isError ? (
          <p role="alert" className="form-error">
            {detail.error.message}
          </p>
        ) : d ? (
          <>
            <h3 className="inv-detail-name">
              {d.name} <span className="inv-code">{d.lot_code}</span>
            </h3>
            <dl className="inv-detail-grid">
              <div>
                <dt>來源</dt>
                <dd>
                  {d.source
                    ? `${d.source.kind === "CONSIGNOR" ? "寄售人" : "賣方"}：${d.source.name ?? "—"}${d.source.phone ? `（${d.source.phone}）` : ""}`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>品牌 / 成色</dt>
                <dd>
                  {brandName(d.brand_id)}・{gradeLabel(d.grade)}
                </dd>
              </div>
              <div>
                <dt>收購成本</dt>
                <dd>
                  <MoneyText value={d.acquisition_cost} />
                </dd>
              </div>
              <div>
                <dt>每件均一價</dt>
                <dd>
                  <MoneyText value={d.unit_price} />
                </dd>
              </div>
              <div>
                <dt>剩餘 / 總數</dt>
                <dd>
                  {d.remaining_qty} / {d.total_qty}
                </dd>
              </div>
              <div>
                <dt>入庫時間</dt>
                <dd>{dt(d.intake_date)}</dd>
              </div>
            </dl>
            <h4 className="inv-detail-subtitle">庫存異動歷史</h4>
            <HistoryList history={d.history} />
          </>
        ) : null}
      </div>
    </div>
  );
}

function SerializedPanel() {
  const [status, setStatus] = useState<SerializedStatus | "">("");
  const [ownership, setOwnership] = useState<Ownership | "">("");
  const [brandId, setBrandId] = useState<number | "">("");
  const [categoryId, setCategoryId] = useState<number | "">("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [detailId, setDetailId] = useState<number | null>(null);
  const { brands, categories } = useFilterOptions();
  const brandName = (id: number | null) =>
    id === null ? "—" : (brands.find((b) => b.id === id)?.name ?? "—");
  const categoryName = (id: number | null) =>
    id === null ? "—" : (categories.find((c) => c.id === id)?.name ?? "—");

  const query = useQuery({
    queryKey: ["inventory", "serialized", { status, ownership, brandId, categoryId, q, page }],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/serialized-items", {
        params: {
          query: {
            status: orUndefined(status),
            ownership: orUndefined(ownership),
            brand_id: brandId === "" ? undefined : brandId,
            category_id: categoryId === "" ? undefined : categoryId,
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
        <select
          aria-label="品牌"
          value={brandId}
          onChange={(e) => { setBrandId(e.target.value === "" ? "" : Number(e.target.value)); setPage(0); }}
        >
          <option value="">全部品牌</option>
          {brands.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <select
          aria-label="類型"
          value={categoryId}
          onChange={(e) => { setCategoryId(e.target.value === "" ? "" : Number(e.target.value)); setPage(0); }}
        >
          <option value="">全部類型</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </SearchBar>
      <TableShell
        loading={query.isFetching}
        error={query.isError ? query.error.message : null}
        empty={rows.length === 0}
        headers={["序號碼", "品名", "成色", "持有", "狀態", "標價", "操作"]}
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
            <td className="inv-row-actions">
              <button type="button" className="btn-ghost" onClick={() => setDetailId(item.id)}>
                詳細
              </button>
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
      {detailId !== null && (
        <ItemDetailModal
          itemId={detailId}
          brandName={brandName}
          categoryName={categoryName}
          onClose={() => setDetailId(null)}
        />
      )}
    </div>
  );
}

function CatalogPanel() {
  const [lowStock, setLowStock] = useState(false);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [detailId, setDetailId] = useState<number | null>(null);

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
        headers={["SKU", "品名", "單價", "現有量", "再訂購點", "操作"]}
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
              <td className="inv-row-actions">
                {low && <BadgeChip badge={{ label: "低庫存", tone: "warn" }} />}
                <button type="button" className="btn-ghost" onClick={() => setDetailId(product.id)}>
                  詳細
                </button>
              </td>
            </tr>
          );
        })}
      </TableShell>
      <Pagination page={page} count={rows.length} onPage={setPage} />
      {detailId !== null && (
        <CatalogDetailModal productId={detailId} onClose={() => setDetailId(null)} />
      )}
    </div>
  );
}

function BulkPanel() {
  const [status, setStatus] = useState<BulkStatus | "">("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [detailId, setDetailId] = useState<number | null>(null);
  const { brands } = useFilterOptions();
  const brandName = (id: number | null) =>
    id === null ? "—" : (brands.find((b) => b.id === id)?.name ?? "—");

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
        headers={["批號", "名稱", "成色", "均一價", "剩餘/總", "收購成本", "售出進度", "狀態", "操作"]}
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
            <td className="inv-row-actions">
              <button type="button" className="btn-ghost" onClick={() => setDetailId(lot.id)}>
                詳細
              </button>
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
      {detailId !== null && (
        <BulkDetailModal lotId={detailId} brandName={brandName} onClose={() => setDetailId(null)} />
      )}
    </div>
  );
}

// 久滯庫存（#4）：只看在庫（IN_STOCK），撈入庫 ≥ N 天、以入庫最久排序；快捷天數＋自訂天數。
function AgingPanel() {
  const [minDays, setMinDays] = useState(90);
  const [customDays, setCustomDays] = useState("");
  const [brandId, setBrandId] = useState<number | "">("");
  const [categoryId, setCategoryId] = useState<number | "">("");
  const [page, setPage] = useState(0);
  const [detailId, setDetailId] = useState<number | null>(null);
  const { brands, categories } = useFilterOptions();
  const brandName = (id: number | null) =>
    id === null ? "—" : (brands.find((b) => b.id === id)?.name ?? "—");
  const categoryName = (id: number | null) =>
    id === null ? "—" : (categories.find((c) => c.id === id)?.name ?? "—");

  const query = useQuery({
    queryKey: ["inventory", "aging", { minDays, brandId, categoryId, page }],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/serialized-items", {
        params: {
          query: {
            status: "IN_STOCK",
            min_age_days: minDays,
            oldest_first: true,
            brand_id: brandId === "" ? undefined : brandId,
            category_id: categoryId === "" ? undefined : categoryId,
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          },
        },
      });
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取久滯庫存失敗");
    },
  });
  const rows: SerializedItem[] = query.data ?? [];

  function applyDays(days: number) {
    setMinDays(days);
    setPage(0);
  }

  return (
    <div className="inv-panel">
      <div className="inv-aging-controls">
        <span className="field-label">入庫超過</span>
        {AGE_PRESETS.map((d) => (
          <button
            key={d}
            type="button"
            className={`chip ${minDays === d ? "chip-active" : ""}`}
            onClick={() => applyDays(d)}
          >
            {d} 天
          </button>
        ))}
        <form
          className="inv-aging-custom"
          onSubmit={(e) => {
            e.preventDefault();
            const n = Number.parseInt(customDays, 10);
            if (Number.isFinite(n) && n >= 1) applyDays(n);
          }}
        >
          <input
            inputMode="numeric"
            placeholder="自訂天數"
            aria-label="自訂天數"
            value={customDays}
            onChange={(e) => setCustomDays(e.target.value)}
          />
          <button type="submit" className="btn-secondary">
            套用
          </button>
        </form>
        <span className="hint">目前：入庫 ≥ {minDays} 天</span>
      </div>
      <div className="inv-filters">
        <select
          aria-label="品牌"
          value={brandId}
          onChange={(e) => { setBrandId(e.target.value === "" ? "" : Number(e.target.value)); setPage(0); }}
        >
          <option value="">全部品牌</option>
          {brands.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <select
          aria-label="類型"
          value={categoryId}
          onChange={(e) => { setCategoryId(e.target.value === "" ? "" : Number(e.target.value)); setPage(0); }}
        >
          <option value="">全部類型</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </div>
      <TableShell
        loading={query.isFetching}
        error={query.isError ? query.error.message : null}
        empty={rows.length === 0}
        headers={["序號碼", "品名", "成色", "持有", "標價", "入庫時間", "已在庫天數", "操作"]}
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
              <MoneyText value={item.listed_price} />
            </td>
            <td>{dt(item.intake_date)}</td>
            <td>
              <span className="inv-age-days">{daysInStock(item.intake_date)} 天</span>
            </td>
            <td className="inv-row-actions">
              <button type="button" className="btn-ghost" onClick={() => setDetailId(item.id)}>
                詳細
              </button>
            </td>
          </tr>
        ))}
      </TableShell>
      <Pagination page={page} count={rows.length} onPage={setPage} />
      {detailId !== null && (
        <ItemDetailModal
          itemId={detailId}
          brandName={brandName}
          categoryName={categoryName}
          onClose={() => setDetailId(null)}
        />
      )}
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
      {tab === "aging" && <AgingPanel />}
      {tab === "catalog" && <CatalogPanel />}
      {tab === "bulk" && <BulkPanel />}
    </section>
  );
}
