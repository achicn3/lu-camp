"use client";
// /acquisition 收購鑑價入庫（docs/10 §/acquisition）：賣方查找/建檔 → 買斷/寄售/散裝 → 鑑價列
// （品牌/型號/分類 combobox + 雙重約束定價輔助）→ 撥款（現金/購物金/混合）→ 送出。
// 全中文（labels 單一真實來源）；金額整數元、走 OpenAPI 生成型別 client；標籤列印待後端（不放假按鈕）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";

import { CreatableCombobox, type ComboOption } from "@/features/acquisition/CreatableCombobox";
import { ACQ_TYPE_LABEL, GRADE_LABEL, PAYOUT_LABEL, SERIALIZED_GRADES } from "@/features/acquisition/labels";
import {
  creditPremiumPreview,
  marginPct,
  maxAcquisitionCost,
  payableTotal,
  suggestedListedPrice,
} from "@/features/acquisition/pricing";
import {
  type AcqType,
  type AcquisitionDraft,
  type ItemDraft,
  type LotDraft,
  validateDraft,
} from "@/features/acquisition/validation";
import { VoidAcquisitionSection } from "@/features/acquisition/VoidAcquisitionSection";
import { VoidConfirmDialog } from "@/features/acquisition/VoidConfirmDialog";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import { formatNtd, parseNtd } from "@/lib/money";
import { newIdempotencyKey } from "@/lib/uuid";

type Contact = components["schemas"]["ContactRead"];
type Category = components["schemas"]["CategoryRead"];
type PricingRule = components["schemas"]["PricingRuleRead"];
type Grade = components["schemas"]["Grade"];
type PayoutMethod = components["schemas"]["PayoutMethod"];

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

function emptyItem(): ItemDraft & { estimatedResale: string } {
  return {
    name: "",
    grade: "",
    categoryId: null,
    brandId: null,
    productModelId: null,
    listedPrice: "",
    acquisitionCost: "",
    commissionPct: "50",
    estimatedResale: "",
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
    label: "",
  };
}

type Row = ItemDraft & { estimatedResale: string };

// ── 賣方/寄售人 ──
function SellerSection({
  isConsignment,
  seller,
  onSelect,
}: {
  isConsignment: boolean;
  seller: Contact | null;
  onSelect: (c: Contact | null) => void;
}) {
  const [q, setQ] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const results = useQuery({
    queryKey: ["contacts-search", q],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/contacts", { params: { query: { q } } });
      return data ?? [];
    },
    enabled: q.trim().length > 0 && seller === null,
  });

  const createMut = useMutation({
    mutationFn: async (input: { name: string; national_id: string }) => {
      const role = isConsignment ? "CONSIGNOR" : "SELLER";
      const { data, error: apiErr } = await api.POST("/api/v1/contacts", {
        body: { name: input.name, national_id: input.national_id, roles: [role], member_points: 0 },
      });
      if (!data) throw new Error(detail(apiErr) ?? "建立失敗");
      return data;
    },
    onSuccess: (c) => {
      onSelect(c);
      setShowCreate(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  if (seller !== null) {
    return (
      <div className="card acq-seller">
        <div>
          <strong>{seller.name}</strong>
          <span className="hint"> {seller.has_national_id ? "（已建檔）" : ""}</span>
        </div>
        <button type="button" className="btn-ghost" onClick={() => onSelect(null)}>
          更換
        </button>
      </div>
    );
  }

  function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") ?? "").trim();
    const nid = String(form.get("national_id") ?? "").trim();
    if (!name || !nid) {
      setError("姓名與身分證字號皆必填");
      return;
    }
    createMut.mutate({ name, national_id: nid });
  }

  return (
    <div className="card">
      <h2>{isConsignment ? "寄售人" : "賣方"}</h2>
      <input
        className="acq-search"
        placeholder="以姓名搜尋"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        aria-label="賣方搜尋"
      />
      {(results.data ?? []).length > 0 && (
        <ul className="acq-results">
          {(results.data ?? []).map((c) => (
            <li key={c.id}>
              <button type="button" className="combo-option" onClick={() => onSelect(c)}>
                {c.name} {c.national_id_masked ? `（${c.national_id_masked}）` : ""}
              </button>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="btn-ghost" onClick={() => setShowCreate((v) => !v)}>
        找不到？建立新{isConsignment ? "寄售人" : "賣方"}
      </button>
      {showCreate && (
        <form className="acq-create-seller" onSubmit={onCreate}>
          <input name="name" placeholder="姓名" aria-label="姓名" />
          <input name="national_id" placeholder="身分證字號" aria-label="身分證字號" />
          <button type="submit" className="btn-primary" disabled={createMut.isPending}>
            建立並選取
          </button>
          {error !== null && <p className="form-error">{error}</p>}
        </form>
      )}
    </div>
  );
}

// ── 鑑價列（買斷/寄售）──
function ItemRowCard({
  type,
  index,
  row,
  categories,
  onChange,
  onRemove,
  refreshCategories,
}: {
  type: AcqType;
  index: number;
  row: Row;
  categories: Category[];
  onChange: (patch: Partial<Row>) => void;
  onRemove: () => void;
  refreshCategories: () => void;
}) {
  const category = categories.find((c) => c.id === row.categoryId) ?? null;

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
  const listed = parseNtd(row.listedPrice);
  const margin = listed !== null && cost !== null ? marginPct(listed, cost) : null;

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
        <label className="field">
          <span className="field-label">品名</span>
          <input
            aria-label="品名"
            value={row.name}
            onChange={(e) => onChange({ name: e.target.value })}
          />
        </label>
        <CreatableCombobox
          label="品牌"
          search={searchBrands}
          create={createBrand}
          placeholder="選擇或新增品牌"
          onChange={(o) => onChange({ brandId: o?.id ?? null, productModelId: null })}
        />
        <CreatableCombobox
          label="型號"
          search={searchModels}
          create={createModel}
          placeholder={row.brandId === null ? "先選品牌" : "選擇或新增型號"}
          disabled={row.brandId === null}
          onChange={(o) => onChange({ productModelId: o?.id ?? null })}
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
          onChange={(o) => onChange({ categoryId: o?.id ?? null })}
        />
        <label className="field">
          <span className="field-label">成色</span>
          <select value={row.grade} onChange={(e) => onChange({ grade: e.target.value as Grade })}>
            <option value="">請選擇</option>
            {SERIALIZED_GRADES.map((g) => (
              <option key={g} value={g}>
                {GRADE_LABEL[g]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">估計轉售價（鑑價輔助）</span>
          <input
            aria-label="估計轉售價"
            inputMode="numeric"
            value={row.estimatedResale}
            onChange={(e) => onChange({ estimatedResale: e.target.value })}
          />
        </label>
      </div>

      {maxCost !== null && (
        <p className="acq-aid">
          建議最高收購成本：<strong className="money">{formatNtd(maxCost)}</strong>
          {category !== null ? `（${category.name}・${row.grade} 級規則）` : ""}
        </p>
      )}

      {type === "BUYOUT" ? (
        <label className="field">
          <span className="field-label">收購價</span>
          <input
            aria-label="收購價"
            inputMode="numeric"
            value={row.acquisitionCost}
            onChange={(e) => onChange({ acquisitionCost: e.target.value })}
          />
          {overCost && <span className="form-error acq-warn">超過建議最高收購成本，毛利偏低</span>}
        </label>
      ) : (
        <label className="field">
          <span className="field-label">抽成 %（寄售）</span>
          <input
            inputMode="numeric"
            value={row.commissionPct}
            onChange={(e) => onChange({ commissionPct: e.target.value })}
          />
        </label>
      )}

      <label className="field">
        <span className="field-label">
          上架售價
          {category !== null && cost !== null && (
            <button
              type="button"
              className="acq-link"
              onClick={() =>
                onChange({
                  listedPrice: String(
                    suggestedListedPrice(cost, category.target_margin_pct) ?? cost,
                  ),
                })
              }
            >
              套用建議（目標毛利 {category.target_margin_pct}%）
            </button>
          )}
        </span>
        <input
          aria-label="上架售價"
          inputMode="numeric"
          value={row.listedPrice}
          onChange={(e) => onChange({ listedPrice: e.target.value })}
        />
        {margin !== null && (
          <span className="hint">
            毛利 {margin}%
            {category !== null && margin < category.target_margin_pct ? "（低於目標）" : ""}
          </span>
        )}
      </label>
    </div>
  );
}

export default function AcquisitionPage() {
  const queryClient = useQueryClient();
  const [type, setType] = useState<AcqType>("BUYOUT");
  const [seller, setSeller] = useState<Contact | null>(null);
  const [rows, setRows] = useState<Row[]>([emptyItem()]);
  const [lot, setLot] = useState<LotDraft>(emptyLot());
  const [payoutMethod, setPayoutMethod] = useState<PayoutMethod>("CASH");
  const [splitCash, setSplitCash] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [result, setResult] = useState<{
    acquisitionId: number;
    codes: string[];
    lot: string | null;
  } | null>(null);
  // 作廢剛建立的這筆（限管理者）：開啟確認對話框／顯示作廢結果。
  const [voidTarget, setVoidTarget] = useState<number | null>(null);
  const [voidedNote, setVoidedNote] = useState<string | null>(null);
  // 送出成功後遞增 → 重掛鑑價列/散裝表單，連同 combobox 內部文字一併清空（避免顯示舊值卻無 id）。
  const [formKey, setFormKey] = useState(0);
  // 管理者才顯示作廢入口（後端 ManagerDep 為最終權威；前端隱藏僅為 UX）。token 在頁面生命週期內不變。
  const isManager = useMemo(() => decodeSession()?.role === "MANAGER", []);

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.GET("/api/v1/settings")).data ?? null,
  });
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

  const isConsignment = type === "CONSIGNMENT";
  const isBulk = type === "BULK_LOT";
  const sellerIsMember = seller?.roles.includes("MEMBER") ?? false;
  const premiumRate = settings.data ? Number(settings.data.premium_rate) : 0;
  const drawerOpen = drawer.data != null;

  const payable = isBulk
    ? parseNtd(lot.acquisitionCost) ?? 0
    : payableTotal(rows.map((r) => parseNtd(r.acquisitionCost) ?? 0));
  const creditEquiv =
    payoutMethod === "STORE_CREDIT"
      ? payable
      : payoutMethod === "SPLIT"
        ? Math.max(0, payable - (parseNtd(splitCash) ?? 0))
        : 0;
  const premiumGain = creditEquiv > 0 ? creditPremiumPreview(creditEquiv, premiumRate) : 0;

  const draft: AcquisitionDraft = {
    type,
    contactId: seller?.id ?? null,
    items: rows,
    lot,
    payoutMethod,
    payoutSplitCash: splitCash,
    sellerIsMember,
  };

  const submit = useMutation({
    mutationFn: async () => {
      const ntd = (s: string) => String(parseNtd(s));
      const body: Record<string, unknown> = { type, contact_id: seller?.id };
      if (isBulk) {
        body.lot = {
          name: lot.name,
          acquisition_cost: ntd(lot.acquisitionCost),
          acquisition_basis: lot.acquisitionBasis,
          total_qty: parseNtd(lot.totalQty),
          unit_price: ntd(lot.unitPrice),
          brand_id: lot.brandId,
          category_id: lot.categoryId,
          label: lot.label || null,
        };
      } else {
        body.items = rows.map((r) => ({
          name: r.name,
          grade: r.grade,
          listed_price: ntd(r.listedPrice),
          brand_id: r.brandId,
          product_model_id: r.productModelId,
          category_id: r.categoryId,
          ...(type === "BUYOUT"
            ? { acquisition_cost: ntd(r.acquisitionCost) }
            : { commission_pct: parseNtd(r.commissionPct) }),
        }));
      }
      if (!isConsignment) {
        body.payout_method = payoutMethod;
        if (payoutMethod === "SPLIT") body.payout_split_cash = ntd(splitCash);
      }
      const { data, error } = await api.POST("/api/v1/acquisitions", {
        body: body as never,
        params: { header: { "Idempotency-Key": newIdempotencyKey() } },
      });
      if (!data) throw new Error(detail(error) ?? "收購送出失敗");
      return data;
    },
    onSuccess: (data) => {
      setResult({ acquisitionId: data.acquisition_id, codes: data.item_codes, lot: data.lot_code });
      setVoidedNote(null);
      setRows([emptyItem()]);
      setLot(emptyLot());
      setSeller(null);
      setFormKey((k) => k + 1);
      void queryClient.invalidateQueries({ queryKey: ["cash-session"] });
    },
    onError: (e: Error) => setErrors([e.message]),
  });

  function onSubmit() {
    setErrors([]);
    setResult(null);
    const found = validateDraft(draft);
    if (!isConsignment && (payoutMethod === "CASH" || payoutMethod === "SPLIT") && !drawerOpen) {
      found.push("現金/混合撥款需先開帳（前往現金對帳開帳）");
    }
    if (found.length > 0) {
      setErrors(found);
      return;
    }
    submit.mutate();
  }

  function patchRow(index: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  return (
    <section className="acq">
      <h1 className="page-title">收購鑑價入庫</h1>

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

      <SellerSection isConsignment={isConsignment} seller={seller} onSelect={setSeller} />

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
            <ItemRowCard
              key={`${formKey}-${i}`}
              type={type}
              index={i}
              row={row}
              categories={categoriesQuery.data ?? []}
              onChange={(patch) => patchRow(i, patch)}
              onRemove={() => setRows((prev) => prev.filter((_, j) => j !== i))}
              refreshCategories={() =>
                void queryClient.invalidateQueries({ queryKey: ["categories"] })
              }
            />
          ))}
          <button type="button" className="btn-ghost" onClick={() => setRows((p) => [...p, emptyItem()])}>
            ＋ 新增一列
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
                  checked={payoutMethod === m}
                  onChange={() => setPayoutMethod(m)}
                />
                {PAYOUT_LABEL[m]}
              </label>
            ))}
          </div>
          <p>
            應付現金總額：<strong className="money">{formatNtd(payable)}</strong>
          </p>
          {payoutMethod === "SPLIT" && (
            <label className="field">
              <span className="field-label">現金部分</span>
              <input inputMode="numeric" value={splitCash} onChange={(e) => setSplitCash(e.target.value)} />
            </label>
          )}
          {(payoutMethod === "STORE_CREDIT" || payoutMethod === "SPLIT") && (
            <p className="acq-premium">
              {sellerIsMember
                ? `購物金入帳 ${formatNtd(creditEquiv + premiumGain)}（含溢價可多得 ${formatNtd(premiumGain)}，依當前溢價率試算）`
                : "提醒：購物金/混合撥款的對象必須是會員"}
            </p>
          )}
          {(payoutMethod === "CASH" || payoutMethod === "SPLIT") && !drawerOpen && (
            <p className="form-error">尚未開帳：現金/混合撥款需先至「現金對帳」開帳</p>
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

      <button
        type="button"
        className="btn-primary acq-submit"
        onClick={onSubmit}
        disabled={submit.isPending}
      >
        送出收購
      </button>

      {result !== null && (
        <div className="card form-success acq-result">
          <p>收購完成（單號 #{result.acquisitionId}）。</p>
          {result.codes.length > 0 && <p>序號條碼：{result.codes.join("、")}</p>}
          {result.lot !== null && <p>散裝批號：{result.lot}</p>}
          <p className="hint">標籤列印功能待後端端點上線後提供。</p>
          {voidedNote === null && isManager && (
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

      {isManager && <VoidAcquisitionSection />}
    </section>
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
  return (
    <div className="card acq-row">
      <h2>散裝批</h2>
      <div className="acq-row-grid">
        <label className="field">
          <span className="field-label">名稱</span>
          <input value={lot.name} onChange={(e) => patch({ name: e.target.value })} />
        </label>
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
          onChange={(o) => patch({ categoryId: o?.id ?? null })}
        />
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
          <input inputMode="numeric" value={lot.unitPrice} onChange={(e) => patch({ unitPrice: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">命名（選填）</span>
          <input value={lot.label} onChange={(e) => patch({ label: e.target.value })} />
        </label>
      </div>
    </div>
  );
}
