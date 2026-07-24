"use client";
// /acquisition 收購鑑價入庫（docs/10 §/acquisition）：賣方查找/建檔 → 買斷/寄售/散裝 → 鑑價列
// （品牌/型號/分類 combobox + 雙重約束定價輔助）→ 撥款（現金/購物金/混合）→ 送出。
// 全中文（labels 單一真實來源）；金額整數元、走 OpenAPI 生成型別 client；標籤列印待後端（不放假按鈕）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState, useSyncExternalStore } from "react";

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
import { canVoid } from "@/features/acquisition/void";
import { isValidNationalId } from "@/features/member/national-id";
import { terminalInstallationId } from "@/features/customer-display/PosCustomerDisplay";
import { VoidAcquisitionSection } from "@/features/acquisition/VoidAcquisitionSection";
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
type ContactRole = components["schemas"]["ContactRole"];
type Category = components["schemas"]["CategoryRead"];
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
  const role: ContactRole = isConsignment ? "CONSIGNOR" : "SELLER";
  const roleLabel = isConsignment ? "寄售人" : "賣方";

  const results = useQuery({
    queryKey: ["contacts-search", q],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/contacts", { params: { query: { q } } });
      return data ?? [];
    },
    enabled: q.trim().length > 0 && seller === null,
  });

  const createMut = useMutation({
    mutationFn: async (input: { name: string; phone: string; national_id: string }) => {
      const { data, error: apiErr } = await api.POST("/api/v1/contacts", {
        body: {
          name: input.name,
          phone: input.phone,
          national_id: input.national_id,
          roles: [role],
          member_points: 0,
        },
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

  // 補登：為已選取、但尚無身分證字號的既有會員設定 national_id 並加上賣方/寄售人角色
  // （收購櫃檯一條龍；後端放寬 CLERK 可補登，仍寫稽核）。
  const backfillMut = useMutation({
    mutationFn: async (input: { id: number; national_id: string }) => {
      const roles = Array.from(
        new Set<ContactRole>([...((seller?.roles ?? []) as ContactRole[]), role]),
      );
      const { data, error: apiErr } = await api.PATCH("/api/v1/contacts/{contact_id}", {
        params: { path: { contact_id: input.id } },
        body: { national_id: input.national_id, roles },
      });
      if (!data) throw new Error(detail(apiErr) ?? "補登失敗");
      return data;
    },
    onSuccess: (c) => onSelect(c),
    onError: (e: Error) => setError(e.message),
  });

  function onBackfill(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (seller === null) return;
    const nid = String(new FormData(event.currentTarget).get("national_id") ?? "").trim();
    if (!isValidNationalId(nid)) {
      setError("身分證字號格式或檢核碼不正確，請確認後重新輸入");
      return;
    }
    backfillMut.mutate({ id: seller.id, national_id: nid });
  }

  if (seller !== null) {
    return (
      <div className="card acq-seller">
        <div className="acq-seller-head">
          <div>
            <strong>{seller.name}</strong>
            {seller.phone && <span className="hint"> · {seller.phone}</span>}
            <span className="hint">
              {" "}
              {seller.has_national_id ? "（已建檔）" : "（尚未建檔身分證）"}
            </span>
          </div>
          <button type="button" className="btn-ghost" onClick={() => onSelect(null)}>
            更換
          </button>
        </div>
        {!seller.has_national_id && (
          <form className="acq-backfill" onSubmit={onBackfill}>
            <div className="acq-backfill-row">
              <input
                name="national_id"
                placeholder="補登身分證字號"
                aria-label="補登身分證字號"
                autoComplete="off"
                maxLength={10}
              />
              <button type="submit" className="btn-primary" disabled={backfillMut.isPending}>
                補登並設為{roleLabel}
              </button>
            </div>
            {error !== null && (
              <p role="alert" className="form-error">
                {error}
              </p>
            )}
          </form>
        )}
      </div>
    );
  }

  function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") ?? "").trim();
    const phone = String(form.get("phone") ?? "").trim();
    const nid = String(form.get("national_id") ?? "").trim();
    if (!name || !phone || !nid) {
      setError("姓名、電話、身分證字號皆必填");
      return;
    }
    if (!isValidNationalId(nid)) {
      setError("身分證字號格式或檢核碼不正確，請確認後重新輸入");
      return;
    }
    createMut.mutate({ name, phone, national_id: nid });
  }

  return (
    <div className="card">
      <h2>{roleLabel}</h2>
      <input
        className="acq-search"
        placeholder="以手機或姓名搜尋"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        aria-label="賣方搜尋"
      />
      {(results.data ?? []).length > 0 && (
        <ul className="acq-results">
          {(results.data ?? []).map((c) => (
            <li key={c.id}>
              <button type="button" className="combo-option" onClick={() => onSelect(c)}>
                {c.name}
                {c.phone ? ` · ${c.phone}` : ""}
                {c.national_id_masked ? `（${c.national_id_masked}）` : "（無證號）"}
              </button>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="btn-ghost" onClick={() => setShowCreate((v) => !v)}>
        找不到？建立新{roleLabel}
      </button>
      {showCreate && (
        <form className="acq-create-seller" onSubmit={onCreate}>
          <input name="name" placeholder="姓名" aria-label="姓名" />
          <input name="phone" placeholder="手機" aria-label="手機" inputMode="tel" />
          <input name="national_id" placeholder="身分證字號" aria-label="身分證字號" maxLength={10} />
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

// ── 標籤列印（Brother 標籤機）：收購完成後，逐一補印序號品 / 散裝批的條碼標籤 ──
function PrintLabelsAction({ codes, lot }: { codes: string[]; lot: string | null }) {
  const total = codes.length + (lot !== null ? 1 : 0);

  const print = useMutation({
    mutationFn: async () => {
      let sent = 0;
      for (const code of codes) {
        const { data, error } = await api.GET("/api/v1/serialized-items/by-code/{item_code}", {
          params: { path: { item_code: code } },
        });
        if (!data) throw new Error(detail(error) ?? `查無序號品 ${code}`);
        await printLabel(code, data.name, parseNtd(data.listed_price) ?? 0);
        sent += 1;
      }
      if (lot !== null) {
        const { data, error } = await api.GET("/api/v1/bulk-lots/by-code/{lot_code}", {
          params: { path: { lot_code: lot } },
        });
        if (!data) throw new Error(detail(error) ?? `查無散裝批 ${lot}`);
        await printLabel(lot, data.name, parseNtd(data.unit_price) ?? 0);
        sent += 1;
      }
      return sent;
    },
  });

  if (total === 0) return null;

  return (
    <div className="acq-print-labels">
      <button
        type="button"
        className="btn-secondary"
        onClick={() => print.mutate()}
        disabled={print.isPending}
      >
        {print.isPending ? "列印中…" : `列印標籤（${total} 張）`}
      </button>
      {print.isSuccess && (
        <p className="form-success">已送出 {print.data} 張標籤。</p>
      )}
      {print.isError && (
        <p className="form-error">列印失敗：{print.error.message}</p>
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
  const [payoutMethod, setPayoutMethod] = useState<PayoutMethod>("CASH");
  const [splitCash, setSplitCash] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [result, setResult] = useState<{
    acquisitionId: number;
    type: AcquisitionType;
    codes: string[];
    lot: string | null;
    /** 撥入購物金實發額（後端帳本分錄 signed_amount；非購物金撥款為 null）。 */
    creditGranted: string | null;
    /** 撥入後購物金總額（後端帳本分錄 balance_after；非購物金撥款為 null）。 */
    creditBalanceAfter: string | null;
  } | null>(null);
  // 作廢剛建立的這筆（限管理者）：開啟確認對話框／顯示作廢結果。
  const [voidTarget, setVoidTarget] = useState<number | null>(null);
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
  const sellerIsMember = seller?.roles.includes("MEMBER") ?? false;
  const premiumRate = settings.data ? Number(settings.data.premium_rate) : 0;
  const drawerOpen = drawer.data != null;

  const payable = isBulk
    ? parseNtd(lot.acquisitionCost) ?? 0
    : payableTotal(rows.map((r) => parseNtd(r.acquisitionCost) ?? 0));
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
    items: rows,
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
        body.payout_method = effectivePayout;
        if (effectivePayout === "SPLIT") body.payout_split_cash = ntd(splitCash);
      }
      if (signed && signTaskId != null) body.signature_task_id = signTaskId;
      // 本次掛載已有鍵（就地重試，含記憶體後備）就沿用，否則新鑄並標記本掛載擁有。
      const key = loadPendingAcqIdemKey() ?? newIdempotencyKey();
      const durable = savePendingAcqIdemKey(key);
      setSessionOwnsKey(true);
      setIdemNotDurable(!durable); // 未持久化：提示跨重整保護不保證（本 session 仍防重複）。
      const { data, error, response } = await api.POST("/api/v1/acquisitions", {
        body: body as never,
        params: { header: { "Idempotency-Key": key } },
      });
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
    onSuccess: (data) => {
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
        type: data.type,
        codes: data.item_codes,
        lot: data.lot_code,
        creditGranted: data.payout_credit_granted,
        creditBalanceAfter: data.payout_credit_balance_after,
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
    setSeller(null);
    setSignTaskId(null);
    setFormKey((k) => k + 1);
  };

  // 推送手持切結任務（docs/23 K4）：以當前鑑價內容建立 AFFIDAVIT 任務給手持裝置簽署。
  const pushSign = useMutation({
    mutationFn: async () => {
      if (!seller) throw new Error("請先選擇賣方");
      const items = isBulk
        ? [{ name: lot.name || "散裝批", amount: String(payable) }]
        : rows.map((r) => ({ name: r.name || "品項", amount: String(parseNtd(r.acquisitionCost) ?? 0) }));
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
        throw new Error("請先將此 POS 櫃檯與顧客顯示裝置配對");
      }
      if (!terminal.paired_kiosk.online) {
        throw new Error("顧客顯示裝置目前離線，無法進行收購簽署");
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
                  checked={effectivePayout === m}
                  disabled={signed}
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
                  ? "簽署任務已逾時，請重新送出。"
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
                  : "已送至顧客顯示裝置，等待客人開啟簽署畫面…"}
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

      <button
        type="button"
        className="btn-primary acq-submit"
        onClick={onSubmit}
        disabled={submit.isPending || recoveryNeeded || (signTaskId != null && !signed)}
      >
        送出收購
      </button>

      {result !== null && (
        <div className="card form-success acq-result">
          <p>收購完成（單號 #{result.acquisitionId}）。</p>
          {drawerNotice !== null && (
            <p role="alert" className="form-error">
              錢櫃未開啟：{drawerNotice}（收購已完成，請以鑰匙開櫃付款）
            </p>
          )}
          {result.codes.length > 0 && <p>序號條碼：{result.codes.join("、")}</p>}
          {result.lot !== null && <p>散裝批號：{result.lot}</p>}
          <PrintLabelsAction codes={result.codes} lot={result.lot} />
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
          {voidedNote === null && isManager && canVoid({ voided_at: null, type: result.type }) && (
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
