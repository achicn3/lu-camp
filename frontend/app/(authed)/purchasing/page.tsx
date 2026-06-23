"use client";
// /purchasing 採購/補貨工作台（docs/10 §/purchasing，Phase 5）：低庫存提醒、採購單建立/清單、
// 一次性收貨入庫、供應商建檔。全走 OpenAPI 生成型別 client（docs/11，禁手刻型別）。
// 後端負責交易原子性與重複收貨守衛；前端只做清楚的工作流與防呆（不重複入庫）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";

import {
  type CatalogProduct,
  canReceive,
  canSubmitPo,
  type DraftLine,
  draftTotal,
  lineTotal,
  poStatusBadge,
  qtyError,
  supplierNameError,
  toLinePayload,
  unitCostError,
} from "@/features/purchasing/purchasing";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Supplier = components["schemas"]["SupplierRead"];
type PurchaseOrder = components["schemas"]["PurchaseOrderRead"];

type Tab = "orders" | "suppliers";

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function money(value: string): string {
  const parsed = parseNtd(value);
  return parsed === null ? value : formatNtd(parsed);
}

function dt(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString("zh-TW") : "—";
}

let draftKeySeq = 0;
function nextDraftKey(): string {
  draftKeySeq += 1;
  return `pl-${draftKeySeq}`;
}

// ── 低庫存提醒 ───────────────────────────────────────────────
function LowStockCard() {
  const lowStock = useQuery({
    queryKey: ["catalog-products", "low-stock"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/catalog-products", {
        params: { query: { low_stock: true, limit: 100, offset: 0 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取低庫存清單失敗");
      return data;
    },
  });

  const rows = lowStock.data ?? [];
  return (
    <div className="card pur-lowstock">
      <h2>低庫存提醒</h2>
      {lowStock.isPending ? (
        <p>載入中…</p>
      ) : lowStock.isError ? (
        <p role="alert" className="form-error">
          {lowStock.error.message}
        </p>
      ) : rows.length === 0 ? (
        <p className="empty-state">目前沒有低於補貨點的數量品。</p>
      ) : (
        <ul className="pur-lowstock-list">
          {rows.map((p) => (
            <li key={p.id}>
              <span>{p.name}</span>
              <span className="row-sub">{p.sku}</span>
              <span className="pur-lowstock-qty">
                現量 {p.quantity_on_hand} / 補貨點 {p.reorder_point}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── 採購單明細列（建單暫存） ──────────────────────────────────
function DraftLineRow({
  line,
  onChange,
  onRemove,
}: {
  line: DraftLine;
  onChange: (next: DraftLine) => void;
  onRemove: () => void;
}) {
  const qtyErr = qtyError(line.qty);
  const costErr = unitCostError(line.unitCost);
  const total = lineTotal(line);
  return (
    <tr>
      <td>
        {line.product.name}
        <span className="row-sub">{line.product.sku}</span>
      </td>
      <td>
        <input
          type="number"
          min={1}
          step={1}
          className={`pur-qty ${qtyErr ? "input-error" : ""}`}
          aria-label={`數量 ${line.product.name}`}
          aria-invalid={qtyErr !== null}
          value={Number.isNaN(line.qty) ? "" : line.qty}
          onChange={(e) => onChange({ ...line, qty: Number.parseInt(e.target.value, 10) })}
        />
      </td>
      <td>
        <input
          inputMode="numeric"
          className={`pur-cost ${costErr ? "input-error" : ""}`}
          aria-label={`進貨單價 ${line.product.name}`}
          aria-invalid={costErr !== null}
          value={line.unitCost}
          onChange={(e) => onChange({ ...line, unitCost: e.target.value })}
        />
      </td>
      <td className="money">{total === null ? "—" : formatNtd(total)}</td>
      <td>
        <button type="button" className="btn-ghost" onClick={onRemove} aria-label={`移除 ${line.product.name}`}>
          移除
        </button>
      </td>
    </tr>
  );
}

// ── 建立採購單 ───────────────────────────────────────────────
function CreatePurchaseOrder({ suppliers }: { suppliers: Supplier[] }) {
  const queryClient = useQueryClient();
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [formError, setFormError] = useState<string | null>(null);

  const productSearch = useQuery({
    queryKey: ["catalog-products", "search", search],
    enabled: search.trim().length > 0,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/catalog-products", {
        params: { query: { q: search.trim(), limit: 20, offset: 0 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "搜尋商品失敗");
      return data;
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      if (supplierId === null) throw new Error("請選擇供應商");
      const { data, error } = await api.POST("/api/v1/purchase-orders", {
        body: { supplier_id: supplierId, lines: toLinePayload(lines) },
      });
      if (!data) throw new Error(extractDetail(error) ?? "建立採購單失敗");
      return data;
    },
    onSuccess: () => {
      setLines([]);
      setSearch("");
      setSupplierId(null);
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    },
    onError: (err: Error) => setFormError(err.message),
  });

  function addProduct(product: CatalogProduct) {
    setLines((prev) =>
      prev.some((l) => l.product.id === product.id)
        ? prev
        : [...prev, { key: nextDraftKey(), product, qty: 1, unitCost: "" }],
    );
  }

  const total = draftTotal(lines);
  const submittable = canSubmitPo(supplierId, lines);

  return (
    <div className="card pur-create">
      <h2>建立採購單</h2>
      <label className="field">
        <span>供應商</span>
        <select
          aria-label="供應商"
          value={supplierId ?? ""}
          onChange={(e) => setSupplierId(e.target.value === "" ? null : Number(e.target.value))}
        >
          <option value="">請選擇供應商</option>
          {suppliers.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>

      <label className="field">
        <span>搜尋數量品（加入明細）</span>
        <input
          aria-label="搜尋數量品"
          placeholder="輸入品名或 SKU"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </label>
      {search.trim().length > 0 && (
        <div className="pur-search-results">
          {productSearch.isPending ? (
            <p>搜尋中…</p>
          ) : productSearch.isError ? (
            <p role="alert" className="form-error">
              {productSearch.error.message}
            </p>
          ) : (productSearch.data ?? []).length === 0 ? (
            <p className="empty-state">查無相符的數量品。</p>
          ) : (
            <ul>
              {(productSearch.data ?? []).map((p) => (
                <li key={p.id}>
                  <button type="button" className="btn-ghost" onClick={() => addProduct(p)}>
                    ＋ {p.name} <span className="row-sub">{p.sku}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {lines.length > 0 && (
        <table className="data-table pur-lines">
          <thead>
            <tr>
              <th>商品</th>
              <th>數量</th>
              <th>進貨單價</th>
              <th>小計</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <DraftLineRow
                key={line.key}
                line={line}
                onChange={(next) =>
                  setLines((prev) => prev.map((l) => (l.key === line.key ? next : l)))
                }
                onRemove={() => setLines((prev) => prev.filter((l) => l.key !== line.key))}
              />
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={3}>合計</td>
              <td className="money">{formatNtd(total)}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      )}

      {formError !== null && (
        <p role="alert" className="form-error">
          {formError}
        </p>
      )}
      <button
        type="button"
        className="btn-primary"
        disabled={!submittable || create.isPending}
        onClick={() => create.mutate()}
      >
        {create.isPending ? "建立中…" : "建立採購單"}
      </button>
    </div>
  );
}

// ── 採購單清單 + 收貨 ────────────────────────────────────────
function PurchaseOrderList({ suppliers }: { suppliers: Supplier[] }) {
  const queryClient = useQueryClient();
  const [receiving, setReceiving] = useState<PurchaseOrder | null>(null);
  const [receiveError, setReceiveError] = useState<string | null>(null);

  const supplierName = useMemo(() => {
    const map = new Map(suppliers.map((s) => [s.id, s.name] as const));
    return (id: number) => map.get(id) ?? `#${id}`;
  }, [suppliers]);

  const orders = useQuery({
    queryKey: ["purchase-orders"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/purchase-orders", {
        params: { query: { limit: 100, offset: 0 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取採購單失敗");
      return data;
    },
  });

  const receive = useMutation({
    mutationFn: async (po: PurchaseOrder) => {
      const { data, error } = await api.POST("/api/v1/purchase-orders/{purchase_order_id}/receive", {
        params: { path: { purchase_order_id: po.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "收貨失敗，請重新整理後再試");
      return data;
    },
    onSuccess: () => {
      setReceiving(null);
      setReceiveError(null);
      void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
    },
    onError: (err: Error) => setReceiveError(err.message),
  });

  const rows = orders.data ?? [];

  return (
    <div className="card pur-orders">
      <h2>採購單</h2>
      {orders.isPending ? (
        <p>載入中…</p>
      ) : orders.isError ? (
        <p role="alert" className="form-error">
          {orders.error.message}
        </p>
      ) : rows.length === 0 ? (
        <p className="empty-state">尚無採購單。</p>
      ) : (
        <table className="data-table pur-order-table">
          <thead>
            <tr>
              <th>單號</th>
              <th>供應商</th>
              <th>下單時間</th>
              <th>項數</th>
              <th>總額</th>
              <th>狀態</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((po) => {
              const badge = poStatusBadge(po.status);
              return (
                <tr key={po.id}>
                  <td>#{po.id}</td>
                  <td>{supplierName(po.supplier_id)}</td>
                  <td>
                    {dt(po.ordered_at)}
                    {po.received_at && <span className="row-sub">收貨 {dt(po.received_at)}</span>}
                  </td>
                  <td>{po.lines.length}</td>
                  <td className="money">{money(po.total_cost)}</td>
                  <td>
                    <span className={`inv-badge inv-tone-${badge.tone}`}>{badge.label}</span>
                  </td>
                  <td>
                    {canReceive(po.status) && (
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={receive.isPending}
                        onClick={() => {
                          setReceiving(po);
                          setReceiveError(null);
                        }}
                      >
                        收貨入庫
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {receiving !== null && (
        <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="確認收貨">
          <div className="card pos-dialog">
            <h2>確認收貨入庫</h2>
            <p className="hint">
              採購單 #{receiving.id}（{supplierName(receiving.supplier_id)}）共 {receiving.lines.length} 項、
              合計 <span className="money">{money(receiving.total_cost)}</span>。確認後將補入庫存且無法復原。
            </p>
            {receiveError !== null && (
              <p role="alert" className="form-error">
                {receiveError}
              </p>
            )}
            <div className="pos-dialog-actions">
              <button
                type="button"
                className="btn-primary"
                disabled={receive.isPending}
                onClick={() => receive.mutate(receiving)}
              >
                {receive.isPending ? "收貨中…" : "確認收貨"}
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={receive.isPending}
                onClick={() => {
                  setReceiving(null);
                  setReceiveError(null);
                }}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── 供應商建檔 ───────────────────────────────────────────────
function SupplierManager({ suppliers }: { suppliers: Supplier[] }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [taxId, setTaxId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/suppliers", {
        body: {
          name: name.trim(),
          contact: contact.trim() === "" ? null : contact.trim(),
          tax_id: taxId.trim() === "" ? null : taxId.trim(),
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "建立供應商失敗");
      return data;
    },
    onSuccess: () => {
      setName("");
      setContact("");
      setTaxId("");
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["suppliers"] });
    },
    onError: (err: Error) => setFormError(err.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const nameErr = supplierNameError(name);
    if (nameErr !== null) {
      setFormError(nameErr);
      return;
    }
    create.mutate();
  }

  return (
    <div className="pur-suppliers">
      <form className="card pur-supplier-form" onSubmit={onSubmit}>
        <h2>新增供應商</h2>
        <label className="field">
          <span>名稱 *</span>
          <input aria-label="供應商名稱" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>聯絡方式</span>
          <input aria-label="聯絡方式" value={contact} onChange={(e) => setContact(e.target.value)} />
        </label>
        <label className="field">
          <span>統一編號</span>
          <input aria-label="統一編號" value={taxId} onChange={(e) => setTaxId(e.target.value)} />
        </label>
        {formError !== null && (
          <p role="alert" className="form-error">
            {formError}
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={create.isPending}>
          {create.isPending ? "新增中…" : "新增供應商"}
        </button>
      </form>

      <div className="card pur-supplier-list">
        <h2>供應商清單</h2>
        {suppliers.length === 0 ? (
          <p className="empty-state">尚無供應商。</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>名稱</th>
                <th>聯絡方式</th>
                <th>統編</th>
              </tr>
            </thead>
            <tbody>
              {suppliers.map((s) => (
                <tr key={s.id}>
                  <td>{s.name}</td>
                  <td>{s.contact ?? "—"}</td>
                  <td>{s.tax_id ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── 上架數量型商品（廠商採購商品先建檔，之後才能建採購單→收貨）──────────────
function CreateCatalogProductCard() {
  const queryClient = useQueryClient();
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [unitPrice, setUnitPrice] = useState("");
  const [reorderPoint, setReorderPoint] = useState("0");
  const [formError, setFormError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      const price = parseNtd(unitPrice);
      if (sku.trim() === "" || name.trim() === "") throw new Error("SKU 與品名必填");
      if (price === null || price <= 0) throw new Error("售價請輸入正整數");
      const reorder = parseNtd(reorderPoint) ?? 0;
      const { data, error } = await api.POST("/api/v1/catalog-products", {
        body: {
          sku: sku.trim(),
          name: name.trim(),
          unit_price: price,
          reorder_point: reorder,
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "上架失敗");
      return data;
    },
    onSuccess: (data) => {
      setOkMsg(`已上架「${data.name}」（SKU ${data.sku}），初始庫存 0，可於下方建採購單補貨。`);
      setSku("");
      setName("");
      setUnitPrice("");
      setReorderPoint("0");
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
    },
    onError: (err: Error) => {
      setFormError(err.message);
      setOkMsg(null);
    },
  });

  return (
    <form
      className="card pur-catalog-form"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <h2>上架數量型商品</h2>
      <p className="hint">廠商採購商品先在此建檔（初始庫存 0），之後即可建採購單→收貨補庫存。</p>
      <label className="field">
        <span>SKU *</span>
        <input aria-label="SKU" value={sku} onChange={(e) => setSku(e.target.value)} />
      </label>
      <label className="field">
        <span>品名 *</span>
        <input aria-label="品名" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>售價（含稅整數元）*</span>
        <input
          aria-label="售價"
          inputMode="numeric"
          value={unitPrice}
          onChange={(e) => setUnitPrice(e.target.value)}
        />
      </label>
      <label className="field">
        <span>低庫存提醒點</span>
        <input
          aria-label="低庫存提醒點"
          inputMode="numeric"
          value={reorderPoint}
          onChange={(e) => setReorderPoint(e.target.value)}
        />
      </label>
      {formError !== null && (
        <p role="alert" className="form-error">
          {formError}
        </p>
      )}
      {okMsg !== null && <p className="form-success">{okMsg}</p>}
      <button type="submit" className="btn-primary" disabled={create.isPending}>
        {create.isPending ? "上架中…" : "上架商品"}
      </button>
    </form>
  );
}

export default function PurchasingPage() {
  const [tab, setTab] = useState<Tab>("orders");

  const suppliers = useQuery({
    queryKey: ["suppliers"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/suppliers", {
        params: { query: { limit: 200, offset: 0 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取供應商失敗");
      return data;
    },
  });

  const supplierList = suppliers.data ?? [];

  return (
    <section className="pur-page">
      <h1 className="page-title">採購 / 補貨</h1>

      <div className="settle-tabs" aria-label="採購功能">
        <button
          type="button"
          className={`chip ${tab === "orders" ? "chip-active" : ""}`}
          onClick={() => setTab("orders")}
        >
          採購單
        </button>
        <button
          type="button"
          className={`chip ${tab === "suppliers" ? "chip-active" : ""}`}
          onClick={() => setTab("suppliers")}
        >
          供應商
        </button>
      </div>

      {suppliers.isError && (
        <p role="alert" className="form-error">
          {suppliers.error.message}
        </p>
      )}

      {tab === "orders" ? (
        <div className="pur-grid">
          <LowStockCard />
          <CreateCatalogProductCard />
          <CreatePurchaseOrder suppliers={supplierList} />
          <PurchaseOrderList suppliers={supplierList} />
        </div>
      ) : (
        <SupplierManager suppliers={supplierList} />
      )}
    </section>
  );
}
