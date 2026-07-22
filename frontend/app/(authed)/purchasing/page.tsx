"use client";
// /purchasing 採購/補貨工作台（docs/10 §/purchasing）：低庫存提醒、採購單建立（草稿/送出）、
// 清單/搜尋、分批收貨入庫、取消、供應商建檔。全走 OpenAPI 生成型別 client（docs/11，禁手刻型別）。
// 後端負責交易原子性、待收守衛與狀態機；前端只做清楚的工作流與防呆。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type Dispatch,
  type FormEvent,
  type SetStateAction,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { CreatableCombobox, type ComboOption } from "@/features/acquisition/CreatableCombobox";
import { Pagination } from "@/features/common/Pagination";
import {
  type CatalogProduct,
  canCancel,
  canReceive,
  canSubmit,
  canSubmitPo,
  type DraftLine,
  draftTotal,
  lineRemaining,
  lineTotal,
  poStatusBadge,
  qtyError,
  supplierNameError,
  toLinePayload,
  unitCostError,
} from "@/features/purchasing/purchasing";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import { formatTaipeiDateTime } from "@/lib/datetime";
import {
  canDiscardIdempotencyKey,
  clearPendingCatalogCreate,
  clearPendingReceive,
  loadPendingReceive,
  pendingCatalogCreateServerSnapshot,
  pendingCatalogCreateSnapshot,
  type PendingReceive,
  savePendingCatalogCreate,
  savePendingReceive,
  subscribePendingCatalogCreate,
} from "@/lib/idempotency";
import { formatNtd, parseNtd } from "@/lib/money";
import { useBodyScrollLock } from "@/lib/useBodyScrollLock";
import { newIdempotencyKey } from "@/lib/uuid";

type Supplier = components["schemas"]["SupplierRead"];
type PurchaseOrder = components["schemas"]["PurchaseOrderRead"];
type PoStatus = components["schemas"]["PurchaseOrderStatus"];
type PurchaseOrderReceiveBody = components["schemas"]["ReceivePurchaseOrderRequest"];

type Tab = "orders" | "suppliers";

const PAGE_SIZE = 20;
const RECEIVE_ERROR_CODE_HEADER = "X-Lu-Camp-Error-Code";

// 「待收貨」＝ORDERED＋PARTIAL（部分到貨仍有待收量，不可從待收清單消失）。
const PO_STATUS_FILTERS: { key: string; label: string; statuses: PoStatus[] }[] = [
  { key: "ALL", label: "全部", statuses: [] },
  { key: "DRAFT", label: "草稿", statuses: ["DRAFT"] },
  { key: "OUTSTANDING", label: "待收貨", statuses: ["ORDERED", "PARTIAL"] },
  { key: "RECEIVED", label: "已收貨", statuses: ["RECEIVED"] },
  { key: "CANCELLED", label: "已取消", statuses: ["CANCELLED"] },
];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function canDiscardReceivePending(response: Response): boolean {
  if (canDiscardIdempotencyKey(response.status)) return true;
  if (response.status !== 409) return false;
  const code = response.headers.get(RECEIVE_ERROR_CODE_HEADER);
  // 精確重播若已有同 key＋同 body 的 receipt，後端會回 200；有穩定代碼的其他 409 均已 rollback。
  return code !== null && code !== "IDEMPOTENCY_KEY_CONFLICT";
}

function money(value: string): string {
  const parsed = parseNtd(value);
  return parsed === null ? value : formatNtd(parsed);
}

function dt(value: string | null | undefined): string {
  return formatTaipeiDateTime(value);
}

let draftKeySeq = 0;
function nextDraftKey(): string {
  draftKeySeq += 1;
  return `pl-${draftKeySeq}`;
}

// ── 低庫存提醒 ───────────────────────────────────────────────
// 補貨動線的起點：常駐置頂（不再收在折疊抽屜）。每項「補貨」把該一般商品直接帶入建單草稿，
// 並展開建立採購單面板——把「該補什麼」的訊號與「建採購單」的動作接起來。
function LowStockCard({ onReorder }: { onReorder: (product: CatalogProduct) => void }) {
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
        <p className="empty-state">目前沒有低於補貨點的一般商品。</p>
      ) : (
        <ul className="pur-lowstock-list">
          {rows.map((p) => {
            // 在途已足＝現量＋待到貨已達補貨點：提醒可能無需再採購，避免重複下單。
            const covered = p.quantity_on_hand + p.incoming_qty >= p.reorder_point;
            return (
              <li key={p.id}>
                <span className="pur-lowstock-name">{p.name}</span>
                <span className="row-sub">{p.sku}</span>
                <span className="pur-lowstock-qty">
                  現量 {p.quantity_on_hand} / 補貨點 {p.reorder_point}
                  {p.incoming_qty > 0 && (
                    <span className="pur-incoming">
                      ・待到貨 {p.incoming_qty}
                      {covered && <span className="pur-covered">（在途已足）</span>}
                    </span>
                  )}
                </span>
                <button
                  type="button"
                  className="btn-secondary pur-reorder-btn"
                  onClick={() => onReorder(p)}
                  aria-label={`補貨 ${p.name}`}
                >
                  補貨 →
                </button>
              </li>
            );
          })}
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
// 明細草稿（lines）由外層工作台持有，好讓低庫存「補貨」也能帶入同一張草稿。
function CreatePurchaseOrder({
  lines,
  setLines,
}: {
  lines: DraftLine[];
  setLines: Dispatch<SetStateAction<DraftLine[]>>;
}) {
  const queryClient = useQueryClient();
  const catalogCreateStoreId = decodeSession()?.storeId ?? 0;
  const pendingCatalogCreate = useSyncExternalStore(
    subscribePendingCatalogCreate,
    () => pendingCatalogCreateSnapshot(catalogCreateStoreId),
    pendingCatalogCreateServerSnapshot,
  );
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [newProductOpen, setNewProductOpen] = useState(false);
  const [newProductName, setNewProductName] = useState("");
  const [newProductSku, setNewProductSku] = useState("");
  const [newProductPrice, setNewProductPrice] = useState("");
  const [newProductReorderPoint, setNewProductReorderPoint] = useState("0");
  const [newProductError, setNewProductError] = useState<string | null>(null);
  // 送出成功後遞增 → 重掛供應商 combobox，連同內部文字一併清空（避免顯示舊值卻無 id）。
  const [supplierKey, setSupplierKey] = useState(0);

  // 伺服器端搜尋啟用中供應商（預設 include_inactive=false）：不受前端預載上限影響，
  // 停用者累積也不會把啟用供應商擠出選單（Codex 對抗審 medium）。
  async function searchSuppliers(q: string): Promise<ComboOption[]> {
    const { data } = await api.GET("/api/v1/suppliers", {
      params: { query: { q: q.trim() || undefined, limit: 20, offset: 0 } },
    });
    return (data ?? []).map((s) => ({ id: s.id, name: s.name }));
  }
  function createSupplier(name: string): Promise<ComboOption> {
    return api.POST("/api/v1/suppliers", { body: { name, contact: null, tax_id: null } }).then(
      ({ data, error }) => {
        if (!data) throw new Error(extractDetail(error) ?? "建立供應商失敗");
        void queryClient.invalidateQueries({ queryKey: ["suppliers"] });
        return { id: data.id, name: data.name };
      },
    );
  }

  const productSearchText = pendingCatalogCreate?.body.name ?? search;
  const productSearch = useQuery({
    queryKey: ["catalog-products", "search", productSearchText],
    enabled: pendingCatalogCreate === null && productSearchText.trim().length > 0,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/catalog-products", {
        params: { query: { q: productSearchText.trim(), limit: 20, offset: 0 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "搜尋商品失敗");
      return data;
    },
  });

  const create = useMutation({
    mutationFn: async (submit: boolean) => {
      if (supplierId === null) throw new Error("請選擇供應商");
      const { data, error } = await api.POST("/api/v1/purchase-orders", {
        body: { supplier_id: supplierId, lines: toLinePayload(lines), submit },
      });
      if (!data) throw new Error(extractDetail(error) ?? "建立採購單失敗");
      return data;
    },
    onSuccess: () => {
      setLines([]);
      setSearch("");
      setSupplierId(null);
      setSupplierKey((k) => k + 1);
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      // 建立採購單會改變在途待到貨量：一併刷新低庫存提醒（待到貨欄）。
      void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
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

  const createProduct = useMutation({
    mutationFn: async () => {
      if (catalogCreateStoreId === 0) throw new Error("無法取得目前店別，請重新登入");
      let pending = pendingCatalogCreate;
      if (pending === null) {
        const name = newProductName.trim();
        const unitPrice = parseNtd(newProductPrice);
        const reorderPoint = parseNtd(newProductReorderPoint);
        if (name === "") throw new Error("請輸入一般商品名稱");
        if (unitPrice === null || unitPrice <= 0) throw new Error("售價請輸入正整數");
        if (reorderPoint === null || reorderPoint < 0)
          throw new Error("低庫存提醒點請輸入零或正整數");
        pending = {
          key: newIdempotencyKey(),
          body: {
            sku: newProductSku.trim() || null,
            name,
            unit_price: unitPrice,
            reorder_point: reorderPoint,
          },
        };
        savePendingCatalogCreate(catalogCreateStoreId, pending);
      }
      const { data, error, response } = await api.POST("/api/v1/catalog-products", {
        params: { header: { "Idempotency-Key": pending.key } },
        body: pending.body,
      });
      if (!data) {
        if (
          canDiscardIdempotencyKey(response.status) ||
          (response.status === 409 && pending.body.sku !== null)
        ) {
          clearPendingCatalogCreate(catalogCreateStoreId);
        }
        throw new Error(extractDetail(error) ?? "建立一般商品失敗");
      }
      return data;
    },
    onSuccess: (product) => {
      clearPendingCatalogCreate(catalogCreateStoreId);
      addProduct(product);
      setSearch("");
      setNewProductOpen(false);
      setNewProductName("");
      setNewProductSku("");
      setNewProductPrice("");
      setNewProductReorderPoint("0");
      setNewProductError(null);
      void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
    },
    onError: (err: Error) => setNewProductError(err.message),
  });

  function openNewProduct() {
    setNewProductName(search.trim());
    setNewProductSku("");
    setNewProductPrice("");
    setNewProductReorderPoint("0");
    setNewProductError(null);
    setNewProductOpen(true);
  }

  const total = draftTotal(lines);
  const submittable = canSubmitPo(supplierId, lines);

  return (
    <div className="card pur-create">
      <h2>建立採購單</h2>
      <CreatableCombobox
        key={supplierKey}
        label="供應商"
        search={searchSuppliers}
        create={createSupplier}
        placeholder="選擇或新增供應商"
        onChange={(o) => setSupplierId(o?.id ?? null)}
      />

      <label className="field">
        <span>搜尋一般商品（加入明細）</span>
        <input
          aria-label="搜尋一般商品"
          placeholder="輸入品名或 SKU"
          value={productSearchText}
          disabled={pendingCatalogCreate !== null || createProduct.isPending}
          onChange={(e) => {
            setSearch(e.target.value);
            setNewProductOpen(false);
          }}
        />
      </label>
      {productSearchText.trim().length > 0 && (
        <div
          className={`pur-search-results ${newProductOpen || pendingCatalogCreate !== null ? "pur-search-results--create" : ""}`}
        >
          {pendingCatalogCreate === null && productSearch.isPending ? (
            <p>搜尋中…</p>
          ) : pendingCatalogCreate === null && productSearch.isError ? (
            <p role="alert" className="form-error">
              {productSearch.error.message}
            </p>
          ) : pendingCatalogCreate !== null || (productSearch.data ?? []).length === 0 ? (
            <div className="pur-product-empty">
              <p className="empty-state">
                {pendingCatalogCreate !== null
                  ? "上一筆商品建立結果尚未確認，已還原原送出內容。"
                  : "查無相符的一般商品。"}
              </p>
              {!newProductOpen && pendingCatalogCreate === null ? (
                <button type="button" className="btn-secondary" onClick={openNewProduct}>
                  ＋ 建立一般商品
                </button>
              ) : (
                <form
                  className="pur-product-create"
                  onSubmit={(event) => {
                    event.preventDefault();
                    createProduct.mutate();
                  }}
                >
                  <div className="pur-product-create-head">
                    <div>
                      <h3>建立一般商品</h3>
                      <p className="hint">SKU 可留白，由系統自動產生；建立後會直接加入本張採購單。</p>
                    </div>
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={createProduct.isPending || pendingCatalogCreate !== null}
                      onClick={() => setNewProductOpen(false)}
                    >
                      取消
                    </button>
                  </div>
                  <div className="pur-product-create-grid">
                    <label className="field">
                      <span>品名 *</span>
                      <input
                        autoFocus
                        aria-label="一般商品名稱"
                        value={pendingCatalogCreate?.body.name ?? newProductName}
                        disabled={pendingCatalogCreate !== null || createProduct.isPending}
                        onChange={(event) => setNewProductName(event.target.value)}
                      />
                    </label>
                    <label className="field">
                      <span>SKU（選填）</span>
                      <input
                        aria-label="一般商品 SKU"
                        placeholder="留白由系統產生"
                        value={pendingCatalogCreate?.body.sku ?? newProductSku}
                        disabled={pendingCatalogCreate !== null || createProduct.isPending}
                        onChange={(event) => setNewProductSku(event.target.value)}
                      />
                    </label>
                    <label className="field">
                      <span>售價（含稅整數元）*</span>
                      <input
                        aria-label="一般商品售價"
                        inputMode="numeric"
                        value={pendingCatalogCreate?.body.unit_price ?? newProductPrice}
                        disabled={pendingCatalogCreate !== null || createProduct.isPending}
                        onChange={(event) => setNewProductPrice(event.target.value)}
                      />
                    </label>
                    <label className="field">
                      <span>低庫存提醒點</span>
                      <input
                        aria-label="一般商品低庫存提醒點"
                        inputMode="numeric"
                        value={pendingCatalogCreate?.body.reorder_point ?? newProductReorderPoint}
                        disabled={pendingCatalogCreate !== null || createProduct.isPending}
                        onChange={(event) => setNewProductReorderPoint(event.target.value)}
                      />
                    </label>
                  </div>
                  {newProductError !== null && (
                    <p role="alert" className="form-error">
                      {newProductError}
                    </p>
                  )}
                  <button type="submit" className="btn-secondary" disabled={createProduct.isPending}>
                    {createProduct.isPending
                      ? "建立中…"
                      : pendingCatalogCreate !== null
                        ? "重試並確認建立結果"
                        : "建立並加入採購單"}
                  </button>
                </form>
              )}
            </div>
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
        <div className="pur-lines-wrap">
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
        </div>
      )}

      {formError !== null && (
        <p role="alert" className="form-error">
          {formError}
        </p>
      )}
      <div className="pur-create-actions">
        <button
          type="button"
          className="btn-secondary"
          disabled={!submittable || create.isPending || createProduct.isPending}
          onClick={() => create.mutate(false)}
        >
          {create.isPending ? "處理中…" : "存草稿"}
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={!submittable || create.isPending || createProduct.isPending}
          onClick={() => create.mutate(true)}
        >
          {create.isPending ? "處理中…" : "送出採購"}
        </button>
      </div>
    </div>
  );
}

// ── 採購單詳情（點單號/詳細）：供應商、狀態、時間、逐項訂購/已收/待收＋收貨批次 ──────
function PurchaseOrderDetailModal({
  po,
  productLabel,
  onClose,
  onReceive,
  onCancel,
  cancelPending,
}: {
  po: PurchaseOrder;
  productLabel: (id: number) => CatalogProduct | null;
  onClose: () => void;
  onReceive: () => void;
  onCancel: () => void;
  cancelPending: boolean;
}) {
  const badge = poStatusBadge(po.status);
  useBodyScrollLock(true);
  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="採購單詳情">
      <div className="card pos-dialog pur-detail">
        <div className="pur-detail-head">
          <h2>採購單 #{po.id}</h2>
          <button type="button" className="btn-ghost" onClick={onClose}>
            關閉
          </button>
        </div>
        <dl className="pur-detail-grid">
          <div>
            <dt>供應商</dt>
            <dd>{po.supplier_name}</dd>
          </div>
          <div>
            <dt>狀態</dt>
            <dd>
              <span className={`inv-badge inv-tone-${badge.tone}`}>{badge.label}</span>
            </dd>
          </div>
          <div>
            <dt>{po.status === "DRAFT" ? "建立時間" : "下單時間"}</dt>
            <dd>{dt(po.status === "DRAFT" ? po.created_at : po.ordered_at)}</dd>
          </div>
          <div>
            <dt>收貨完成</dt>
            <dd>{dt(po.received_at)}</dd>
          </div>
        </dl>
        <table className="data-table pur-detail-table">
          <thead>
            <tr>
              <th>商品</th>
              <th>訂購</th>
              <th>已收</th>
              <th>待收</th>
              <th>進貨單價</th>
              <th>小計</th>
            </tr>
          </thead>
          <tbody>
            {po.lines.map((line) => {
              const prod = productLabel(line.catalog_product_id);
              const remaining = lineRemaining(line.qty, line.received_qty);
              return (
                <tr key={line.id}>
                  <td>
                    {prod ? prod.name : `#${line.catalog_product_id}`}
                    {prod && <span className="row-sub">{prod.sku}</span>}
                  </td>
                  <td>{line.qty}</td>
                  <td>{line.received_qty}</td>
                  <td className={remaining > 0 ? "pur-remaining" : ""}>{remaining}</td>
                  <td className="money">{money(line.unit_cost)}</td>
                  <td className="money">{money(line.line_total)}</td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={5}>合計</td>
              <td className="money">{money(po.total_cost)}</td>
            </tr>
          </tfoot>
        </table>

        {po.receipts.length > 0 && (
          <div className="pur-receipts">
            <h3>收貨批次</h3>
            <ul className="pur-receipts-list">
              {po.receipts.map((r, idx) => (
                <li key={r.id}>
                  <span className="pur-receipt-head">
                    第 {idx + 1} 批・{dt(r.received_at)}
                  </span>
                  {r.invoice ? (
                    <span className="row-sub">
                      發票 {r.invoice.invoice_number}（{r.invoice.invoice_date}）含稅{" "}
                      {money(r.invoice.invoice_total)}｜未稅 {money(r.invoice.invoice_net)}／稅{" "}
                      {money(r.invoice.invoice_tax)}
                    </span>
                  ) : (
                    <BackfillInvoiceForm poId={po.id} receiptId={r.id} />
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {(canReceive(po.status) || canCancel(po.status)) && (
          <div className="pos-dialog-actions">
            {canReceive(po.status) && (
              <button type="button" className="btn-primary" onClick={onReceive}>
                收貨入庫
              </button>
            )}
            {canCancel(po.status) && (
              <button
                type="button"
                className="btn-ghost pur-cancel-btn"
                disabled={cancelPending}
                onClick={onCancel}
              >
                {cancelPending ? "取消中…" : "取消採購單"}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── 進項發票補登（某收貨批次漏登時；登錄後不可覆寫）─────────────
function BackfillInvoiceForm({ poId, receiptId }: { poId: number; receiptId: number }) {
  const queryClient = useQueryClient();
  const [number, setNumber] = useState("");
  const [dateStr, setDateStr] = useState("");
  const [total, setTotal] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const backfill = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST(
        "/api/v1/purchase-orders/{purchase_order_id}/receipts/{receipt_id}/invoice",
        {
          params: { path: { purchase_order_id: poId, receipt_id: receiptId } },
          body: {
            invoice_number: number.trim().toUpperCase(),
            invoice_date: dateStr,
            invoice_total: total.trim(),
          },
        },
      );
      if (!data) throw new Error(extractDetail(error) ?? "補登失敗");
      return data;
    },
    onSuccess: () => {
      setNote("已補登進項發票");
      void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    },
    onError: (e: Error) => setNote(e.message),
  });
  return (
    <div className="pur-backfill-invoice">
      <h3>補登進項發票</h3>
      <div className="pur-invoice-row">
        <input
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          placeholder="AB12345678"
          maxLength={10}
          aria-label="補登發票號碼"
        />
        <input
          type="date"
          value={dateStr}
          onChange={(e) => setDateStr(e.target.value)}
          aria-label="補登發票日期"
        />
        <input
          value={total}
          onChange={(e) => setTotal(e.target.value)}
          inputMode="numeric"
          placeholder="含稅金額"
          aria-label="補登發票含稅金額"
        />
        <button
          type="button"
          className="btn-secondary"
          disabled={backfill.isPending || !number || !dateStr || !total}
          onClick={() => backfill.mutate()}
        >
          補登
        </button>
      </div>
      {note !== null && <p className="hint">{note}</p>}
    </div>
  );
}

// ── 採購單清單 + 分批收貨 + 送出/取消 ────────────────────────
function PurchaseOrderList() {
  const queryClient = useQueryClient();
  const [receiving, setReceiving] = useState<PurchaseOrder | null>(null);
  const [receiveError, setReceiveError] = useState<string | null>(null);
  // 本次各明細實收量（line_id → 字串）。開啟收貨對話框時預設帶入待收量。
  const [receiveQty, setReceiveQty] = useState<Record<number, string>>({});
  // 進項發票（選填；三欄全空＝不登錄，可事後補登）。開啟/取消都清空避免跨單殘留誤登。
  const [invNumber, setInvNumber] = useState("");
  const [invDate, setInvDate] = useState("");
  const [invTotal, setInvTotal] = useState("");
  const [rowError, setRowError] = useState<string | null>(null);
  // 復原提示（非錯誤）：偵測到上一次未確認收貨、已和解時告知店員確認剩餘數量。
  const [receiveNotice, setReceiveNotice] = useState<string | null>(null);
  useBodyScrollLock(receiving !== null); // 收貨對話框開啟時鎖背景捲動
  const resetInvoiceDraft = () => {
    setInvNumber("");
    setInvDate("");
    setInvTotal("");
  };
  function openReceive(po: PurchaseOrder) {
    resetInvoiceDraft();
    setReceiveNotice(null);
    setReceiveQty(
      Object.fromEntries(
        po.lines.map((l) => [l.id, String(lineRemaining(l.qty, l.received_qty))]),
      ),
    );
    setReceiving(po);
    setReceiveError(null);
  }
  // 預設「待收貨」＝ORDERED＋PARTIAL——最常用；要看全部/草稿/已取消可切籤。
  const [statusKey, setStatusKey] = useState("OUTSTANDING");
  const [page, setPage] = useState(0);
  const [detailPo, setDetailPo] = useState<PurchaseOrder | null>(null);
  // 單號/供應商搜尋（提交式）：輸入框與已提交值分開，避免每次按鍵都打 API。
  const [search, setSearch] = useState("");
  const [submittedSearch, setSubmittedSearch] = useState("");
  const statuses = useMemo(
    () => PO_STATUS_FILTERS.find((f) => f.key === statusKey)?.statuses ?? [],
    [statusKey],
  );

  // 一般商品名稱對照（採購單明細以 catalog_product_id 記錄；詳情頁顯示品名/SKU）。
  const catalog = useQuery({
    queryKey: ["catalog-products", "name-map"],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/catalog-products", {
        params: { query: { limit: 200, offset: 0 } },
      });
      return data ?? [];
    },
  });
  const productLabel = useMemo(() => {
    const map = new Map((catalog.data ?? []).map((p) => [p.id, p] as const));
    return (id: number) => map.get(id) ?? null;
  }, [catalog.data]);

  const orders = useQuery({
    queryKey: ["purchase-orders", statusKey, submittedSearch, page],
    queryFn: async () => {
      const query: { limit: number; offset: number; status?: PoStatus[]; q?: string } = {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      };
      if (statuses.length > 0) query.status = statuses;
      if (submittedSearch) query.q = submittedSearch;
      const { data, error } = await api.GET("/api/v1/purchase-orders", {
        params: { query },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取採購單失敗");
      return data;
    },
  });

  const invalidateOrders = () => {
    void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
  };

  const receive = useMutation({
    mutationFn: async (po: PurchaseOrder): Promise<{ reconciled: boolean }> => {
      const receiveUrl = "/api/v1/purchase-orders/{purchase_order_id}/receive" as const;
      // 1) 先和解上一次未確認的收貨：以「原 body＋原鍵」重播（冪等）。避免重整後 PO 待收量已變、
      //    卻以新待收量沿用舊鍵重送而永久 409 卡死（Codex 第三輪）。
      const pending = loadPendingReceive(po.id);
      if (pending) {
        const { data, response } = await api.POST(receiveUrl, {
          params: {
            path: { purchase_order_id: po.id },
            header: { "Idempotency-Key": pending.key },
          },
          body: pending.body as PurchaseOrderReceiveBody,
        });
        if (data) {
          clearPendingReceive(po.id);
          return { reconciled: true };
        }
        // 重播非成功：僅「確定未提交」的 4xx 可丟棄舊鍵、續本次收貨；否則保留、請店員稍後再試。
        if (!canDiscardReceivePending(response)) {
          throw new Error("上一次收貨狀態未定，請稍後再試。");
        }
        clearPendingReceive(po.id);
      }
      // 2) 本次收貨（新鍵）
      const parsedLines = po.lines.map((line) => {
        const raw = (receiveQty[line.id] ?? "").trim();
        const qty = raw === "" ? 0 : Number(raw);
        if (!Number.isFinite(qty) || !Number.isInteger(qty) || qty < 0) {
          throw new Error("本次實收量必須為正整數");
        }
        return { line, qty };
      });
      const lines = parsedLines
        .filter(({ qty }) => qty > 0)
        .map(({ line, qty }) => ({ line_id: line.id, qty }));
      if (lines.length === 0) throw new Error("請至少輸入一項的本次實收量");
      for (const { line, qty } of parsedLines) {
        if (qty > lineRemaining(line.qty, line.received_qty)) {
          throw new Error("本次實收量不可超過待收量");
        }
      }
      const hasInvoice = invNumber.trim() !== "" || invDate !== "" || invTotal.trim() !== "";
      const body: PurchaseOrderReceiveBody = hasInvoice
        ? {
            lines,
            invoice: {
              invoice_number: invNumber.trim().toUpperCase(),
              invoice_date: invDate,
              invoice_total: invTotal.trim(),
            },
          }
        : { lines };
      const key = newIdempotencyKey();
      // 送出前先連同 body 持久化：回應遺失/重整後由此重播和解，後端只入庫一次（防重複入庫）。
      const entry: PendingReceive = { key, body };
      savePendingReceive(po.id, entry);
      const { data, error, response } = await api.POST(receiveUrl, {
        params: {
          path: { purchase_order_id: po.id },
          header: { "Idempotency-Key": key },
        },
        body,
      });
      if (!data) {
        if (canDiscardReceivePending(response)) clearPendingReceive(po.id);
        throw new Error(extractDetail(error) ?? "收貨失敗，請稍後再試");
      }
      clearPendingReceive(po.id);
      return { reconciled: false };
    },
    onSuccess: (result) => {
      setReceiving(null);
      setReceiveError(null);
      resetInvoiceDraft();
      invalidateOrders();
      setReceiveNotice(
        result.reconciled
          ? "偵測到上一次收貨尚未確認、已為您同步；請確認待收數量後再收剩餘。"
          : null,
      );
    },
    onError: (err: Error) => setReceiveError(err.message),
  });

  const submit = useMutation({
    mutationFn: async (po: PurchaseOrder) => {
      const { data, error } = await api.POST("/api/v1/purchase-orders/{purchase_order_id}/submit", {
        params: { path: { purchase_order_id: po.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "送出失敗");
      return data;
    },
    onSuccess: () => {
      setRowError(null);
      invalidateOrders();
    },
    onError: (err: Error) => setRowError(err.message),
  });

  const cancel = useMutation({
    mutationFn: async (po: PurchaseOrder) => {
      const { data, error } = await api.POST("/api/v1/purchase-orders/{purchase_order_id}/cancel", {
        params: { path: { purchase_order_id: po.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "取消失敗");
      return data;
    },
    onSuccess: () => {
      setRowError(null);
      setDetailPo(null);
      invalidateOrders();
    },
    onError: (err: Error) => setRowError(err.message),
  });

  const rows = orders.data ?? [];
  const busy = submit.isPending || cancel.isPending || receive.isPending;

  return (
    <div className="card pur-orders">
      <h2>採購單</h2>
      <div className="settle-tabs" aria-label="採購單狀態篩選">
        {PO_STATUS_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={`chip ${statusKey === f.key ? "chip-active" : ""}`}
            onClick={() => {
              setStatusKey(f.key);
              setPage(0);
            }}
          >
            {f.label}
          </button>
        ))}
      </div>
      <form
        className="member-allsearch"
        onSubmit={(e) => {
          e.preventDefault();
          setPage(0);
          setSubmittedSearch(search.trim());
        }}
      >
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="以單號或供應商搜尋"
          aria-label="採購單搜尋"
        />
        <button type="submit" className="btn-secondary">
          搜尋
        </button>
        {submittedSearch && (
          <button
            type="button"
            className="btn-ghost"
            onClick={() => {
              setSearch("");
              setSubmittedSearch("");
              setPage(0);
            }}
          >
            清除（{submittedSearch}）
          </button>
        )}
      </form>
      {rowError !== null && (
        <p role="alert" className="form-error">
          {rowError}
        </p>
      )}
      {receiveNotice !== null && (
        <p role="status" className="hint pur-notice">
          {receiveNotice}
        </p>
      )}
      {orders.isPending ? (
        <p>載入中…</p>
      ) : orders.isError ? (
        <p role="alert" className="form-error">
          {orders.error.message}
        </p>
      ) : rows.length === 0 ? (
        <p className="empty-state">
          {page === 0 ? "尚無符合的採購單。" : "沒有更多採購單了。"}
        </p>
      ) : (
        <div className="pur-order-wrap">
        <table className="data-table pur-order-table">
          <thead>
            <tr>
              <th>單號</th>
              <th>供應商</th>
              <th>建立 / 下單時間</th>
              <th>項數</th>
              <th>總額</th>
              <th>狀態</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((po) => {
              const badge = poStatusBadge(po.status);
              return (
                <tr key={po.id}>
                  <td>
                    <button type="button" className="pur-po-link" onClick={() => setDetailPo(po)}>
                      #{po.id}
                    </button>
                  </td>
                  <td>{po.supplier_name}</td>
                  <td>
                    {dt(po.status === "DRAFT" ? po.created_at : po.ordered_at)}
                    {po.received_at && <span className="row-sub">收貨 {dt(po.received_at)}</span>}
                  </td>
                  <td>{po.lines.length}</td>
                  <td className="money">{money(po.total_cost)}</td>
                  <td>
                    <span className={`inv-badge inv-tone-${badge.tone}`}>{badge.label}</span>
                  </td>
                  <td className="pur-row-actions">
                    <button type="button" className="btn-ghost" onClick={() => setDetailPo(po)}>
                      詳細
                    </button>
                    {canSubmit(po.status) && (
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={busy}
                        onClick={() => submit.mutate(po)}
                      >
                        送出
                      </button>
                    )}
                    {canReceive(po.status) && (
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={busy}
                        onClick={() => openReceive(po)}
                      >
                        收貨入庫
                      </button>
                    )}
                    {canCancel(po.status) && (
                      <button
                        type="button"
                        className="btn-ghost pur-cancel-btn"
                        disabled={busy}
                        onClick={() => cancel.mutate(po)}
                      >
                        取消
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
      {!orders.isPending && !orders.isError && (
        <Pagination page={page} count={rows.length} pageSize={PAGE_SIZE} onPage={setPage} />
      )}

      {detailPo !== null && (
        <PurchaseOrderDetailModal
          po={detailPo}
          productLabel={productLabel}
          onClose={() => setDetailPo(null)}
          onReceive={() => {
            const po = detailPo;
            setDetailPo(null);
            openReceive(po);
          }}
          onCancel={() => cancel.mutate(detailPo)}
          cancelPending={cancel.isPending}
        />
      )}

      {receiving !== null && (
        <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="確認收貨">
          <div className="card pos-dialog pur-receive-dialog">
            <h2>收貨入庫</h2>
            <p className="hint">
              採購單 #{receiving.id}（{receiving.supplier_name}）。輸入本次各項實收量，
              未收足將轉為「部分到貨」，可日後再收。
            </p>
            <div className="pur-lines-wrap">
              <table className="data-table pur-receive-table">
                <thead>
                  <tr>
                    <th>商品</th>
                    <th>訂購</th>
                    <th>已收</th>
                    <th>待收</th>
                    <th>本次實收</th>
                  </tr>
                </thead>
                <tbody>
                  {receiving.lines.map((line) => {
                    const prod = productLabel(line.catalog_product_id);
                    const name = prod ? prod.name : `#${line.catalog_product_id}`;
                    const remaining = lineRemaining(line.qty, line.received_qty);
                    return (
                      <tr key={line.id}>
                        <td>{name}</td>
                        <td>{line.qty}</td>
                        <td>{line.received_qty}</td>
                        <td>{remaining}</td>
                        <td>
                          <input
                            type="number"
                            min={0}
                            max={remaining}
                            step={1}
                            className="pur-qty"
                            aria-label={`本次實收 ${name}`}
                            value={receiveQty[line.id] ?? ""}
                            onChange={(e) =>
                              setReceiveQty((prev) => ({ ...prev, [line.id]: e.target.value }))
                            }
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <fieldset className="pur-invoice-fields">
              <legend>進項發票（選填；供應商發票隨貨時一併登錄，漏登可事後補登）</legend>
              <label className="field">
                <span className="field-label">發票號碼（2 英文＋8 數字）</span>
                <input
                  value={invNumber}
                  onChange={(e) => setInvNumber(e.target.value)}
                  placeholder="AB12345678"
                  maxLength={10}
                  aria-label="發票號碼"
                />
              </label>
              <label className="field">
                <span className="field-label">發票日期</span>
                <input
                  type="date"
                  value={invDate}
                  onChange={(e) => setInvDate(e.target.value)}
                  aria-label="發票日期"
                />
              </label>
              <label className="field">
                <span className="field-label">含稅金額（整數元）</span>
                <input
                  value={invTotal}
                  onChange={(e) => setInvTotal(e.target.value)}
                  inputMode="numeric"
                  aria-label="發票含稅金額"
                />
              </label>
            </fieldset>
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
                  resetInvoiceDraft();
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
function SupplierManager() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [taxId, setTaxId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [submittedSearch, setSubmittedSearch] = useState("");
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<Supplier | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  // 管理清單含停用者（include_inactive）；建單供應商選單另走頁面頂層查詢（預設只取啟用中）。
  const list = useQuery({
    queryKey: ["suppliers", "list", submittedSearch, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/suppliers", {
        params: {
          query: {
            q: submittedSearch || undefined,
            include_inactive: true,
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          },
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取供應商失敗");
      return data;
    },
  });
  const rows = list.data ?? [];

  const setActive = useMutation({
    mutationFn: async ({ id, active }: { id: number; active: boolean }) => {
      const params = { params: { path: { supplier_id: id } } };
      const { data, error } = active
        ? await api.POST("/api/v1/suppliers/{supplier_id}/activate", params)
        : await api.POST("/api/v1/suppliers/{supplier_id}/deactivate", params);
      if (!data) throw new Error(extractDetail(error) ?? "更新供應商狀態失敗");
      return data;
    },
    onSuccess: () => {
      setRowError(null);
      void queryClient.invalidateQueries({ queryKey: ["suppliers"] });
    },
    onError: (err: Error) => setRowError(err.message),
  });

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
        <form
          className="member-allsearch"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(0);
            setSubmittedSearch(search.trim());
          }}
        >
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="以名稱搜尋供應商"
            aria-label="供應商搜尋"
          />
          <button type="submit" className="btn-secondary">
            搜尋
          </button>
          {submittedSearch && (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setSearch("");
                setSubmittedSearch("");
                setPage(0);
              }}
            >
              清除（{submittedSearch}）
            </button>
          )}
        </form>
        {list.isPending ? (
          <p>載入中…</p>
        ) : list.isError ? (
          <p role="alert" className="form-error">
            {list.error.message}
          </p>
        ) : rows.length === 0 ? (
          <p className="empty-state">
            {submittedSearch ? "查無符合的供應商。" : "尚無供應商。"}
          </p>
        ) : (
          <>
            {rowError !== null && (
              <p role="alert" className="form-error">
                {rowError}
              </p>
            )}
            <div className="pur-order-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>名稱</th>
                    <th>聯絡方式</th>
                    <th>統編</th>
                    <th>狀態</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.id} className={s.is_active ? "" : "pur-supplier-inactive"}>
                      <td>{s.name}</td>
                      <td>{s.contact ?? "—"}</td>
                      <td>{s.tax_id ?? "—"}</td>
                      <td>
                        <span className={`inv-badge inv-tone-${s.is_active ? "ok" : "muted"}`}>
                          {s.is_active ? "啟用中" : "已停用"}
                        </span>
                      </td>
                      <td className="pur-row-actions">
                        <button
                          type="button"
                          className="btn-ghost"
                          onClick={() => {
                            setRowError(null);
                            setEditing(s);
                          }}
                        >
                          編輯
                        </button>
                        {s.is_active ? (
                          <button
                            type="button"
                            className="btn-ghost pur-supplier-state-btn pur-supplier-state-btn--deactivate"
                            disabled={setActive.isPending}
                            onClick={() => setActive.mutate({ id: s.id, active: false })}
                          >
                            停用
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn-ghost pur-supplier-state-btn pur-supplier-state-btn--activate"
                            disabled={setActive.isPending}
                            onClick={() => setActive.mutate({ id: s.id, active: true })}
                          >
                            啟用
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <Pagination page={page} count={rows.length} pageSize={PAGE_SIZE} onPage={setPage} />
      </div>

      {editing !== null && (
        <SupplierEditModal
          supplier={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void queryClient.invalidateQueries({ queryKey: ["suppliers"] });
          }}
        />
      )}
    </div>
  );
}

// ── 供應商編輯（名稱/聯絡方式/統編）─────────────────────────────
function SupplierEditModal({
  supplier,
  onClose,
  onSaved,
}: {
  supplier: Supplier;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(supplier.name);
  const [contact, setContact] = useState(supplier.contact ?? "");
  const [taxId, setTaxId] = useState(supplier.tax_id ?? "");
  const [error, setError] = useState<string | null>(null);
  useBodyScrollLock(true);

  const update = useMutation({
    mutationFn: async () => {
      const nameErr = supplierNameError(name);
      if (nameErr !== null) throw new Error(nameErr);
      // 只送「有更動」的欄位（稀疏 PATCH）：未動的欄位不重送舊快照，避免蓋掉他人並發修改
      // （Codex 對抗審 medium）。以正規化值比對原值判斷是否更動。
      const nextContact = contact.trim() === "" ? null : contact.trim();
      const nextTaxId = taxId.trim() === "" ? null : taxId.trim();
      const body: components["schemas"]["SupplierUpdate"] = {};
      if (name.trim() !== supplier.name) body.name = name.trim();
      if (nextContact !== (supplier.contact ?? null)) body.contact = nextContact;
      if (nextTaxId !== (supplier.tax_id ?? null)) body.tax_id = nextTaxId;
      if (Object.keys(body).length === 0) return supplier; // 無更動：不打 API
      const { data, error: err } = await api.PATCH("/api/v1/suppliers/{supplier_id}", {
        params: { path: { supplier_id: supplier.id } },
        body,
      });
      if (!data) throw new Error(extractDetail(err) ?? "更新供應商失敗");
      return data;
    },
    onSuccess: onSaved,
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="編輯供應商">
      <div className="card pos-dialog">
        <h2>編輯供應商</h2>
        <label className="field">
          <span>名稱 *</span>
          <input aria-label="編輯供應商名稱" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>聯絡方式</span>
          <input
            aria-label="編輯聯絡方式"
            value={contact}
            onChange={(e) => setContact(e.target.value)}
          />
        </label>
        <label className="field">
          <span>統一編號</span>
          <input
            aria-label="編輯統一編號"
            value={taxId}
            onChange={(e) => setTaxId(e.target.value)}
          />
        </label>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button
            type="button"
            className="btn-primary"
            disabled={update.isPending}
            onClick={() => update.mutate()}
          >
            {update.isPending ? "儲存中…" : "儲存"}
          </button>
          <button type="button" className="btn-ghost" disabled={update.isPending} onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 採購單工作台（採購單分頁主體）───────────────────────────
// 順著補貨動線排：低庫存提醒（起點，可一鍵帶入草稿）→ 建立採購單（主要動作，展開式面板）→
// 採購單清單（待收貨／收貨）。上架一般商品屬主檔建檔，已移至 /庫存「一般商品」分頁。
function OrdersWorkbench() {
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const createRef = useRef<HTMLDivElement>(null);

  function reorder(product: CatalogProduct) {
    setLines((prev) =>
      prev.some((l) => l.product.id === product.id)
        ? prev
        : [...prev, { key: nextDraftKey(), product, qty: 1, unitCost: "" }],
    );
    setCreateOpen(true);
    // 面板可能剛掛上，等下一幀再捲入視野。scrollIntoView 於 jsdom（測試環境）不存在，
    // 以可選呼叫守衛避免拋錯。
    requestAnimationFrame(() =>
      createRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }),
    );
  }

  return (
    <div className="pur-workbench">
      {/* 右側欄（桌面）：低庫存提醒常駐、可 sticky；窄螢幕回到單欄置頂。 */}
      <aside className="pur-workbench-rail">
        <LowStockCard onReorder={reorder} />
      </aside>
      <div className="pur-workbench-main">
        <div ref={createRef} className="pur-create-panel">
          <button
            type="button"
            className="btn-primary pur-create-toggle"
            aria-expanded={createOpen}
            onClick={() => setCreateOpen((o) => !o)}
          >
            {createOpen ? "收合建立採購單" : "＋ 建立採購單"}
          </button>
          {createOpen && <CreatePurchaseOrder lines={lines} setLines={setLines} />}
        </div>
        <PurchaseOrderList />
      </div>
    </div>
  );
}

export default function PurchasingPage() {
  const [tab, setTab] = useState<Tab>("orders");

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

      {tab === "orders" ? <OrdersWorkbench /> : <SupplierManager />}
    </section>
  );
}
