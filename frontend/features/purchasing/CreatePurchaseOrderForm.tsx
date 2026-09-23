"use client";
// 建立採購單（獨立頁）：① 供應商 → ② 明細（搜既有商品，或直接新增商品）→ 存草稿／送出。
//
// 不出現 SKU（2026-09-23 採購改版）：商品條碼由系統自動產生，收貨後在明細頁印標籤。
// 新增商品比照收購頁：品牌 → 型號（品名自動帶型號）→ 分類 → 成本；售價依設定的毛利率、
// 營業稅與行動支付手續費自動算出（進位到 10 元，ADR-023），店員可以直接改。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { CreatableCombobox, type ComboOption } from "@/features/acquisition/CreatableCombobox";
import { suggestedListedPrice } from "@/features/acquisition/pricing";
import {
  type CatalogProduct,
  canSubmitPo,
  type DraftLine,
  draftTotal,
  lineTotal,
  qtyError,
  toLinePayload,
  unitCostError,
} from "@/features/purchasing/purchasing";
import { extractDetail, nextDraftKey, type PurchaseOrder } from "@/features/purchasing/shared";
import { useCatalogBrandNames } from "@/features/purchasing/useCatalogBrandNames";
import { api } from "@/lib/api";
import { decodeSession } from "@/lib/auth";
import {
  canDiscardIdempotencyKey,
  clearPendingCatalogCreate,
  pendingCatalogCreateServerSnapshot,
  pendingCatalogCreateSnapshot,
  savePendingCatalogCreate,
  subscribePendingCatalogCreate,
} from "@/lib/idempotency";
import { formatNtd, parseNtd } from "@/lib/money";
import { newIdempotencyKey } from "@/lib/uuid";

// ── 明細列 ──
function DraftLineRow({
  line,
  brand,
  onChange,
  onRemove,
}: {
  line: DraftLine;
  brand: string | null | undefined;
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
        {brand && <span className="row-sub">{brand}</span>}
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
      <td className="money">{formatNtd(parseNtd(line.product.unit_price) ?? 0)}</td>
      <td className="money">{total === null ? "—" : formatNtd(total)}</td>
      <td>
        <button
          type="button"
          className="btn-ghost"
          onClick={onRemove}
          aria-label={`移除 ${line.product.name}`}
        >
          移除
        </button>
      </td>
    </tr>
  );
}

export interface ReorderItem {
  id: number;
  /** 低庫存提示算好的建議數量（補貨點−現量−在途）；沒帶就補到補貨點。 */
  qty?: number;
}

export function CreatePurchaseOrderForm({
  initialItems = [],
  onCreated,
}: {
  /** 從低庫存「補貨」帶進來的商品，進頁面就先放進明細。 */
  initialItems?: ReorderItem[];
  onCreated: (po: PurchaseOrder) => void;
}) {
  const queryClient = useQueryClient();
  const brandName = useCatalogBrandNames();
  const catalogCreateStoreId = decodeSession()?.storeId ?? 0;
  const pendingCatalogCreate = useSyncExternalStore(
    subscribePendingCatalogCreate,
    () => pendingCatalogCreateSnapshot(catalogCreateStoreId),
    pendingCatalogCreateServerSnapshot,
  );
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [newProductOpen, setNewProductOpen] = useState(false);
  const [newProductName, setNewProductName] = useState("");
  // 品名自動帶型號：記住上次自動填的值，店員自己改過就不再覆蓋。
  const [autoName, setAutoName] = useState("");
  // 售價與毛利率都是「沒碰過就用推導值」：null＝店員還沒自己輸入過。
  const [newProductPrice, setNewProductPrice] = useState<string | null>(null);
  const [newProductCost, setNewProductCost] = useState("");
  const [newProductQty, setNewProductQty] = useState("1");
  const [newProductMargin, setNewProductMargin] = useState<string | null>(null);
  const [newProductReorderPoint, setNewProductReorderPoint] = useState("0");
  const [newProductBrand, setNewProductBrand] = useState<ComboOption | null>(null);
  const [newProductModel, setNewProductModel] = useState<ComboOption | null>(null);
  const [newProductCategory, setNewProductCategory] = useState<ComboOption | null>(null);
  const [newProductError, setNewProductError] = useState<string | null>(null);

  function addProduct(product: CatalogProduct, unitCost = "", qty = 1) {
    setLines((prev) =>
      prev.some((l) => l.product.id === product.id)
        ? prev
        : [...prev, { key: nextDraftKey(), product, qty, unitCost }],
    );
  }

  // 低庫存帶入：只在第一次拿到商品時放進明細（之後店員移除的不會被加回來）。
  const seeded = useRef(false);
  const reorderProducts = useQuery({
    queryKey: ["catalog-products", "reorder", initialItems.map((item) => item.id).join(",")],
    enabled: initialItems.length > 0,
    queryFn: async () =>
      (
        await Promise.all(
          initialItems.map(async ({ id, qty }) => {
            const { data } = await api.GET("/api/v1/catalog-products/{product_id}", {
              params: { path: { product_id: id } },
            });
            // 優先用提示算好的數量（已扣在途）；沒帶就補到補貨點（至少 1）。
            return data
              ? [{ product: data, qty: qty ?? Math.max(1, data.reorder_point - data.quantity_on_hand) }]
              : [];
          }),
        )
      ).flat(),
  });
  useEffect(() => {
    if (seeded.current || !reorderProducts.data) return;
    seeded.current = true;
    const incoming = reorderProducts.data;
    setLines((prev) => [
      ...prev,
      ...incoming
        .filter(({ product }) => !prev.some((l) => l.product.id === product.id))
        .map(({ product, qty }) => ({ key: nextDraftKey(), product, qty, unitCost: "" })),
    ]);
  }, [reorderProducts.data]);

  // 伺服器端搜尋啟用中供應商（預設 include_inactive=false）。
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

  const productSearchText = search;
  // 稅率與手續費率一律從設定讀，不寫死（CLAUDE.md §6/§7.9）。
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.GET("/api/v1/settings")).data ?? null,
  });

  const productSearch = useQuery({
    queryKey: ["catalog-products", "search", productSearchText],
    enabled: productSearchText.trim().length > 0,
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
    onSuccess: (po) => {
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      // 建立採購單會改變在途待到貨量：一併刷新低庫存提醒（待到貨欄）。
      void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
      onCreated(po);
    },
    onError: (err: Error) => setFormError(err.message),
  });

  // 手續費取兩種行動支付的較高者（定價當下不知道客人會刷哪種，取低的會少補）；
  // 讀不到就當 0——寧可少補，也不要在設定沒載入時把價格墊高、讓客人多付。
  const feeRate = (() => {
    const rates = [settings.data?.linepay_fee_pct, settings.data?.taiwanpay_fee_pct]
      .map((r) => (r === undefined ? Number.NaN : Number(r)))
      .filter((r) => Number.isFinite(r) && r >= 0 && r < 1);
    return rates.length > 0 ? Math.max(...rates) : 0;
  })();
  const rawTaxRate = settings.data?.tax_rate;
  const taxRate =
    rawTaxRate != null && String(rawTaxRate).trim() !== "" ? Number(rawTaxRate) : Number.NaN;
  const taxRateValid =
    !settings.isError && Number.isFinite(taxRate) && taxRate >= 0 && taxRate < 1;
  const taxRateLoading = !settings.isFetched;
  const defaultMargin = settings.data?.purchase_default_margin_pct;

  const marginValue =
    newProductMargin ?? (defaultMargin === undefined ? "" : String(defaultMargin));
  const costNum = parseNtd(newProductCost);
  const marginNum = /^\d+$/.test(marginValue.trim()) ? Number(marginValue) : Number.NaN;
  const marginValid = Number.isInteger(marginNum) && marginNum >= 0 && marginNum <= 99;
  const suggestedPrice =
    taxRateValid && costNum !== null && costNum > 0 && marginValid
      ? suggestedListedPrice(costNum, marginNum, taxRate, feeRate)
      : null;
  // 售價顯示值：店員沒自己改過就用建議售價；改過就以他填的為準，不再被自動覆蓋。
  const priceValue = newProductPrice ?? (suggestedPrice === null ? "" : String(suggestedPrice));

  const createProduct = useMutation({
    mutationFn: async () => {
      if (catalogCreateStoreId === 0) throw new Error("無法取得目前店別，請重新登入");
      let pending = pendingCatalogCreate;
      if (pending === null) {
        const name = newProductName.trim();
        const unitPrice = parseNtd(priceValue);
        const reorderPoint = parseNtd(newProductReorderPoint);
        const qty = parseNtd(newProductQty);
        if (name === "") throw new Error("請輸入品名（選了型號會自動帶入）");
        if (unitPrice === null || unitPrice <= 0) throw new Error("售價請輸入正整數");
        if (qty === null || qty <= 0) throw new Error("採購數量請輸入正整數");
        if (reorderPoint === null || reorderPoint < 0)
          throw new Error("低庫存提醒點請輸入零或正整數");
        pending = {
          key: newIdempotencyKey(),
          // 商品編號一律交給系統產生（sku: null）；沒選的品牌／型號／分類**不帶那些鍵**：
          // 待重送的內容與舊版逐位元相同，跨版本重播才不會被後端當成「同鍵不同內容」拒絕。
          body: {
            sku: null,
            name,
            unit_price: unitPrice,
            reorder_point: reorderPoint,
            ...(newProductBrand === null ? {} : { brand_id: newProductBrand.id }),
            ...(newProductModel === null ? {} : { product_model_id: newProductModel.id }),
            ...(newProductCategory === null ? {} : { category_id: newProductCategory.id }),
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
        throw new Error(extractDetail(error) ?? "建立商品失敗");
      }
      return data;
    },
    onSuccess: (product) => {
      clearPendingCatalogCreate(catalogCreateStoreId);
      // 建立時填的進貨成本與數量直接帶進這張採購單的明細，不必再打第二次（裁示 2026-09-16）。
      const cost = parseNtd(newProductCost);
      const qty = parseNtd(newProductQty);
      addProduct(product, cost !== null && cost > 0 ? String(cost) : "", qty ?? 1);
      setSearch("");
      resetNewProduct();
      setNewProductOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
      // 可能剛在表單裡新增了品牌：重抓品牌名稱，明細那一列才顯示得出來。
      void queryClient.invalidateQueries({ queryKey: ["brands"] });
    },
    onError: (err: Error) => setNewProductError(err.message),
  });

  function resetNewProduct() {
    setNewProductName("");
    setAutoName("");
    setNewProductPrice(null);
    setNewProductCost("");
    setNewProductQty("1");
    setNewProductMargin(null);
    setNewProductReorderPoint("0");
    setNewProductBrand(null);
    setNewProductModel(null);
    setNewProductCategory(null);
    setNewProductError(null);
  }

  function openNewProduct() {
    resetNewProduct();
    setNewProductName(search.trim());
    setNewProductOpen(true);
  }

  function chooseModel(option: ComboOption | null) {
    setNewProductModel(option);
    // 品名自動等於型號（比照收購頁）；店員自己改過的品名不覆蓋。
    if (option !== null && (newProductName.trim() === "" || newProductName === autoName)) {
      setNewProductName(option.name);
      setAutoName(option.name);
    }
  }

  const total = draftTotal(lines);
  const submittable = canSubmitPo(supplierId, lines);
  const creating = newProductOpen || pendingCatalogCreate !== null;
  const locked = pendingCatalogCreate !== null || createProduct.isPending;

  return (
    <div className="pur-create-page">
      <section className="card pur-create-step">
        <h2>
          <span className="pur-step-no">1</span>供應商
        </h2>
        <CreatableCombobox
          label="供應商"
          search={searchSuppliers}
          create={createSupplier}
          placeholder="選擇或新增供應商"
          onChange={(o) => setSupplierId(o?.id ?? null)}
        />
      </section>

      <section className="card pur-create-step">
        <h2>
          <span className="pur-step-no">2</span>採購明細
        </h2>

        {lines.length === 0 ? (
          <p className="empty-state">還沒有商品。用下方搜尋加入既有商品，或新增一個商品。</p>
        ) : (
          <div className="pur-lines-wrap">
            <table className="data-table pur-lines">
              <thead>
                <tr>
                  <th>商品</th>
                  <th>數量</th>
                  <th>進貨單價</th>
                  <th>售價</th>
                  <th>小計</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {lines.map((line) => (
                  <DraftLineRow
                    key={line.key}
                    line={line}
                    brand={brandName(line.product.brand_id)}
                    onChange={(next) =>
                      setLines((prev) => prev.map((l) => (l.key === line.key ? next : l)))
                    }
                    onRemove={() => setLines((prev) => prev.filter((l) => l.key !== line.key))}
                  />
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={4}>合計</td>
                  <td className="money">{formatNtd(total)}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        )}

        <div className="pur-add-product">
          <label className="field">
            <span className="field-label">加入既有商品</span>
            <input
              aria-label="搜尋一般商品"
              placeholder="輸入品名、品牌或型號"
              value={productSearchText}
              disabled={locked}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          {!creating && (
            <button type="button" className="btn-secondary" onClick={openNewProduct}>
              ＋ 新增商品
            </button>
          )}
        </div>

        {productSearchText.trim().length > 0 && !creating && (
          <div className="pur-search-results">
            {productSearch.isPending ? (
              <p>搜尋中…</p>
            ) : productSearch.isError ? (
              <p role="alert" className="form-error">
                {productSearch.error.message}
              </p>
            ) : (productSearch.data ?? []).length === 0 ? (
              <p className="empty-state">
                查無相符的商品，可以按「＋ 新增商品」直接建立。
              </p>
            ) : (
              <ul>
                {(productSearch.data ?? []).map((p) => (
                  <li key={p.id}>
                    <button type="button" className="btn-ghost" onClick={() => addProduct(p)}>
                      ＋ {p.name}
                      {brandName(p.brand_id) && (
                        <span className="row-sub">{brandName(p.brand_id)}</span>
                      )}
                      <span className="row-sub">
                        售價 {formatNtd(parseNtd(p.unit_price) ?? 0)}・現量 {p.quantity_on_hand}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {creating && (
          <form
            className="pur-product-create"
            onSubmit={(event) => {
              event.preventDefault();
              createProduct.mutate();
            }}
          >
            <div className="pur-product-create-head">
              <div>
                <h3>新增商品</h3>
                <p className="hint">
                  {pendingCatalogCreate !== null
                    ? "上一筆商品有沒有建好還不確定，已幫你把剛才填的內容放回來，按下方重試確認。"
                    : "條碼由系統自動產生；建立後會直接加入這張採購單，收貨後再印標籤。"}
                </p>
              </div>
              <button
                type="button"
                className="btn-ghost"
                disabled={locked}
                onClick={() => setNewProductOpen(false)}
              >
                取消
              </button>
            </div>
            <div className="pur-product-create-grid">
              <p className="pur-form-section">商品資料</p>
              {/* 品牌／型號／分類：與收購頁同一組主檔與同一個元件（可直接新增）。
                  換品牌要清掉型號，否則會把別牌的型號送出去。 */}
              <CreatableCombobox
                label="品牌"
                selectedId={newProductBrand?.id ?? null}
                disabled={locked}
                search={(q) =>
                  api
                    .GET("/api/v1/brands", { params: { query: { q } } })
                    .then(({ data }) => (data ?? []).map((b) => ({ id: b.id, name: b.name })))
                }
                create={(name) =>
                  api.POST("/api/v1/brands", { body: { name } }).then(({ data, error }) => {
                    if (!data) throw new Error(extractDetail(error) ?? "建立品牌失敗");
                    return { id: data.id, name: data.name };
                  })
                }
                onChange={(option) => {
                  setNewProductBrand(option);
                  setNewProductModel(null);
                }}
              />
              <CreatableCombobox
                label="型號"
                selectedId={newProductModel?.id ?? null}
                // 沒選品牌就不能選型號（比照收購頁）：型號屬於品牌，標籤與篩選才對得起來。
                placeholder={newProductBrand === null ? "先選品牌" : "選擇或新增型號"}
                disabled={newProductBrand === null || locked}
                search={(q) =>
                  api
                    .GET("/api/v1/product-models", {
                      params: { query: { q, brand_id: newProductBrand?.id ?? undefined } },
                    })
                    .then(({ data }) => (data ?? []).map((m) => ({ id: m.id, name: m.name })))
                }
                create={(name) => {
                  if (newProductBrand === null) {
                    return Promise.reject(new Error("請先選擇品牌"));
                  }
                  return api
                    .POST("/api/v1/product-models", {
                      body: { brand_id: newProductBrand.id, name },
                    })
                    .then(({ data, error }) => {
                      if (!data) throw new Error(extractDetail(error) ?? "建立型號失敗");
                      return { id: data.id, name: data.name };
                    });
                }}
                onChange={chooseModel}
              />
              <label className="field">
                <span className="field-label">品名 *（選型號會自動帶入）</span>
                <input
                  aria-label="一般商品名稱"
                  value={pendingCatalogCreate?.body.name ?? newProductName}
                  disabled={locked}
                  onChange={(event) => setNewProductName(event.target.value)}
                />
              </label>
              <CreatableCombobox
                label="分類"
                selectedId={newProductCategory?.id ?? null}
                disabled={locked}
                search={(q) =>
                  api
                    .GET("/api/v1/categories", { params: { query: { q, limit: 200 } } })
                    .then(({ data }) => (data ?? []).map((c) => ({ id: c.id, name: c.name })))
                }
                create={(name) =>
                  api.POST("/api/v1/categories", { body: { name } }).then(({ data, error }) => {
                    if (!data) throw new Error(extractDetail(error) ?? "建立分類失敗");
                    return { id: data.id, name: data.name };
                  })
                }
                onChange={setNewProductCategory}
              />

              <p className="pur-form-section">進貨與定價</p>
              <label className="field">
                <span className="field-label">進貨成本（每件）</span>
                <input
                  aria-label="一般商品進貨成本"
                  inputMode="numeric"
                  value={newProductCost}
                  disabled={locked}
                  onChange={(event) => setNewProductCost(event.target.value)}
                />
              </label>
              <label className="field">
                <span className="field-label">採購數量</span>
                <input
                  aria-label="一般商品採購數量"
                  inputMode="numeric"
                  value={newProductQty}
                  disabled={locked}
                  onChange={(event) => setNewProductQty(event.target.value)}
                />
              </label>
              <label className="field">
                <span className="field-label">目標毛利率（%）</span>
                <input
                  aria-label="一般商品預估毛利率"
                  inputMode="numeric"
                  value={marginValue}
                  disabled={locked}
                  onChange={(event) => setNewProductMargin(event.target.value)}
                />
              </label>
              <label className="field">
                <span className="field-label">售價（含稅）*</span>
                <input
                  aria-label="一般商品售價"
                  inputMode="numeric"
                  value={pendingCatalogCreate?.body.unit_price ?? priceValue}
                  disabled={locked}
                  onChange={(event) => setNewProductPrice(event.target.value)}
                />
              </label>
              {!taxRateValid && (
                <p role="status" className="hint pur-price-hint">
                  {taxRateLoading
                    ? "稅率設定載入中，暫不計算建議售價。"
                    : "讀不到稅率設定，請直接輸入含稅售價。"}
                </p>
              )}
              {marginValue.trim() !== "" && !marginValid && (
                <p role="alert" className="form-error">
                  毛利率請輸入 0–99 的整數
                </p>
              )}
              {suggestedPrice !== null && (
                <p className="hint pur-price-hint">
                  建議售價 <strong className="money">{formatNtd(suggestedPrice)}</strong>
                  （成本 {formatNtd(costNum ?? 0)}・毛利 {marginNum}%・已含營業稅與行動支付手續費，
                  進位到 10 元）。
                  {newProductPrice !== null && newProductPrice !== String(suggestedPrice) ? (
                    <button
                      type="button"
                      className="btn-ghost pur-inline-btn"
                      onClick={() => setNewProductPrice(null)}
                    >
                      改回建議售價
                    </button>
                  ) : (
                    "可直接改售價，改了就以你填的為準。"
                  )}
                </p>
              )}
              <p className="pur-form-section">庫存</p>
              <label className="field">
                <span className="field-label">低庫存提醒點</span>
                <input
                  aria-label="一般商品低庫存提醒點"
                  inputMode="numeric"
                  value={pendingCatalogCreate?.body.reorder_point ?? newProductReorderPoint}
                  disabled={locked}
                  onChange={(event) => setNewProductReorderPoint(event.target.value)}
                />
              </label>
            </div>
            {newProductError !== null && (
              <p role="alert" className="form-error">
                {newProductError}
              </p>
            )}
            <button type="submit" className="btn-primary" disabled={createProduct.isPending}>
              {createProduct.isPending
                ? "建立中…"
                : pendingCatalogCreate !== null
                  ? "重試並確認建立結果"
                  : "建立並加入採購單"}
            </button>
          </form>
        )}
      </section>

      {formError !== null && (
        <p role="alert" className="form-error">
          {formError}
        </p>
      )}
      {/* 新增商品表單開著時不固定在底部：那時要按的是表單自己的「建立並加入」，別被蓋住。 */}
      <div className={`card pur-create-footer ${creating ? "" : "pur-create-footer--sticky"}`}>
        <p>
          共 {lines.length} 項・合計 <strong className="money">{formatNtd(total)}</strong>
        </p>
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
    </div>
  );
}
