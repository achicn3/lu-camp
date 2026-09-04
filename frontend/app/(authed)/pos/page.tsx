"use client";
// /pos 結帳（docs/10 §5、docs/16 §3.2）：掃碼加入購物車（序號品／散裝堆）→ 會員歸戶（選填）
// → 收款（現金／購物金／混合）→ 結帳 POST /sales →（完成後）詢問是否列印商品明細。
// einvoice_enabled=false 時發票區隱藏（顯示「這筆不開發票」），載具輸入待啟用後再開。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type ChangeEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { discountDisplay } from "@/features/campaigns/campaigns";
import { MemberPanel } from "@/features/pos/MemberPanel";
import {
  PosCustomerDisplay,
  restoreLines,
  terminalInstallationId,
} from "@/features/customer-display/PosCustomerDisplay";
import {
  type CartLine,
  addLine,
  cartTotal,
  isGift,
  lineTotal,
  linesWithNotes,
  markAsGift,
  noteAckFingerprint,
  removeLine,
  setQty,
  toSaleLines,
  unmarkGift,
} from "@/features/pos/cart";
import { withFreshNotes } from "@/features/pos/restoreNotes";
import { RESTORE_LOOKUP_TIMEOUT_MS, withDeadline } from "@/lib/deadline";
import {
  type DiscountDraft,
  canonicalAdjustments,
  describeDiscount,
  pruneDiscounts,
  toAdjustmentRequests,
} from "@/features/pos/discounts";
import {
  type DineInSelection,
  type ServiceMode,
  clearDineIn,
  dineInRequestFields,
  validateDineIn,
} from "@/features/pos/dineIn";
import {
  type MixedRemainderMethod,
  type TenderMode,
  changeDue,
  resolvePlan,
  toTenders,
  validatePlan,
} from "@/features/pos/tender";
import {
  openCashDrawer,
  printEInvoice,
  printKitchenTicket,
  printSaleDetail,
} from "@/lib/agent";
import { fetchSignaturePngBase64 } from "@/lib/signature";
import { api } from "@/lib/api";
import { decodeSession } from "@/lib/auth";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd, roundNtdByRate } from "@/lib/money";
import { formatSalePaymentSummary } from "@/lib/payment";
import {
  clearPersistedIdemKey,
  getOrCreatePersistedIdemKey,
} from "@/lib/idempotency";

type SaleRead = components["schemas"]["SaleRead"];
type InvoiceRead = components["schemas"]["InvoiceRead"];
type ContactRead = components["schemas"]["ContactRead"];
type CampaignRead = components["schemas"]["CampaignRead"];
type MenuItemRead = components["schemas"]["MenuItemRead"];
type TerminalRead = components["schemas"]["TerminalRead"];
type DisplayCart =
  | components["schemas"]["CartSessionRead"]
  | components["schemas"]["StaffCartSessionRead"];

/** 證明聯可印：print_mark 且 Amego 回傳的條碼/QR 內容齊備（docs/24）。 */
function invoiceProofPrintable(invoice: components["schemas"]["InvoiceRead"]): boolean {
  return (
    invoice.print_mark &&
    invoice.barcode_text != null &&
    invoice.qrcode_left != null &&
    invoice.qrcode_right != null
  );
}

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function Money({ value }: { value: number }) {
  return <span className="money">${formatNtd(value)}</span>;
}

// ── 掃碼加入購物車 ──
// 序號品 S{店}-{10碼HEX}、散裝 L{店}-{10碼HEX}（acquisition/codes.py）；掃描到完整碼即自動加入。
// 一般商品以 SKU 查（任意字串，掃碼槍尾端 Enter 送出）：序號品 → 散裝 → 一般商品 一格通吃。
const ITEM_CODE_RE = /^[SL]\d+-[0-9A-F]{10}$/;

function ScanBar({
  onResolved,
  disabled = false,
  disabledReason,
}: {
  onResolved: (line: CartLine) => void;
  disabled?: boolean;
  /** 停用的原因。**掃碼槍打進停用的輸入框會整個消失**（沒有錯誤、什麼都沒有），
   *  店員只會覺得「掃了沒反應」，所以一定要說出為什麼、要等什麼。 */
  disabledReason?: string;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async (code: string): Promise<CartLine> => {
      // 先試序號品，再試散裝堆，最後試一般商品 SKU（一格掃碼通吃，docs/10 §3）。
      const serialized = await api.GET(
        "/api/v1/serialized-items/by-code/{item_code}",
        {
          params: { path: { item_code: code } },
        },
      );
      if (serialized.response.status === 200 && serialized.data) {
        const item = serialized.data;
        if (item.status !== "IN_STOCK")
          throw new Error(`${item.item_code} 非在庫（不可售）`);
        const price = parseNtd(item.listed_price) ?? 0;
        return {
          key: `S:${item.item_code}`,
          lineType: "SERIALIZED",
          description: item.name,
          unitPrice: price,
          qty: 1,
          itemCode: item.item_code,
          maxQty: 1,
          note: item.note,
        };
      }
      // 僅 404 才視為「非序號品」改試散裝；其他狀態（401/403/500）如實回報，
      // 不可把後端錯誤偽裝成「找不到此條碼」（Codex F3 P3）。
      if (serialized.response.status !== 404) {
        throw new Error(
          extractDetail(serialized.error) ??
            `查詢失敗（代碼 ${serialized.response.status}）`,
        );
      }
      const bulk = await api.GET("/api/v1/bulk-lots/by-code/{lot_code}", {
        params: { path: { lot_code: code } },
      });
      if (bulk.response.status === 200 && bulk.data) {
        const lot = bulk.data;
        if (lot.remaining_qty <= 0) throw new Error(`${lot.lot_code} 已售罄`);
        return {
          key: `B:${lot.id}`,
          lineType: "BULK_LOT",
          description: lot.name,
          unitPrice: parseNtd(lot.unit_price) ?? 0,
          qty: 1,
          bulkLotId: lot.id,
          maxQty: lot.remaining_qty,
          note: lot.note,
        };
      }
      if (bulk.response.status !== 404) {
        throw new Error(
          extractDetail(bulk.error) ??
            `查詢失敗（代碼 ${bulk.response.status}）`,
        );
      }
      // 最後試一般商品（SKU）：廠商採購品（瓦斯罐/糧食等）在 POS 直接掃售。
      const catalog = await api.GET("/api/v1/catalog-products/by-sku/{sku}", {
        params: { path: { sku: code } },
      });
      if (catalog.response.status === 200 && catalog.data) {
        const product = catalog.data;
        if (product.quantity_on_hand <= 0)
          throw new Error(`${product.sku} 已無庫存`);
        return {
          key: `C:${product.id}`,
          lineType: "CATALOG",
          description: product.name,
          unitPrice: parseNtd(product.unit_price) ?? 0,
          qty: 1,
          catalogProductId: product.id,
          maxQty: product.quantity_on_hand,
          note: product.note,
        };
      }
      if (catalog.response.status !== 404) {
        throw new Error(
          extractDetail(catalog.error) ??
            `查詢失敗（代碼 ${catalog.response.status}）`,
        );
      }
      throw new Error(`找不到此條碼：${code}`);
    },
    onSuccess: (line) => {
      setError(null);
      setCode("");
      onResolved(line);
    },
    onError: (err: Error) => setError(err.message),
  });

  function submit(raw: string) {
    const value = raw.trim();
    if (!value || mutation.isPending || disabled) return;
    mutation.mutate(value);
  }

  function onChange(event: ChangeEvent<HTMLInputElement>) {
    const value = event.target.value;
    // 掃碼槍：輸入到完整碼制即自動送出、清空（免按 Enter）；清空後若掃碼槍補送 Enter 也是空字串、無副作用。
    if (ITEM_CODE_RE.test(value.trim())) {
      setCode("");
      submit(value);
      return;
    }
    setCode(value);
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit(code);
  }

  return (
    <form className="pos-scan" onSubmit={onSubmit}>
      <label className="field">
        <span className="field-label">掃描或輸入商品條碼</span>
        {/* 櫃檯掃碼槍輸入，聚焦為核心操作（docs/10 §3）：掃到完整碼自動加入，免按 Enter。 */}
        <input
          name="code"
          className="pos-scan-input"
          value={code}
          onChange={onChange}
          autoFocus
          inputMode="text"
          autoComplete="off"
          placeholder="掃描商品條碼，或手動輸入後按 Enter"
          disabled={mutation.isPending || disabled}
        />
      </label>
      <span className="hint pos-scan-hint">
        {disabled
          ? (disabledReason ?? "簽署或付款處理期間，購物車已鎖定。")
          : mutation.isPending
            ? "查詢中…"
            : "掃描後自動加入購物車（免按 Enter）。"}
      </span>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </form>
  );
}

// ── 收款 ──
function TenderPanel({
  total,
  hasMember,
  memberBalance,
  drawerOpen,
  storeCreditMax,
  storeCreditMinSpend,
  cartHasItems,
  taiwanpayFeePct,
  linepayEnabled,
  linepayFeePct,
  linePayKey,
  setLinePayKey,
  mode,
  setMode,
  storeCreditInput,
  setStoreCreditInput,
  mixedRemainder,
  setMixedRemainder,
  taiwanPayConfirmed,
  setTaiwanPayConfirmed,
  receivedInput,
  setReceivedInput,
  disabled = false,
}: {
  total: number;
  hasMember: boolean;
  memberBalance: number | null;
  drawerOpen: boolean | null;
  storeCreditMax: number;
  storeCreditMinSpend: number;
  cartHasItems: boolean;
  /** 台灣Pay 手續費率（小數，如 0.02=2%；docs/30）。僅供顯示店家負擔，不向客人收取。 */
  taiwanpayFeePct: string;
  /** LINE Pay 是否啟用（docs/30）：未啟用時不顯示 LINE Pay 收款選項。 */
  linepayEnabled: boolean;
  /** LINE Pay 手續費率（小數）。僅供顯示店家負擔。 */
  linepayFeePct: string;
  /** LINE Pay 掃到的客人一次性付款碼（oneTimeKey）。 */
  linePayKey: string;
  setLinePayKey: (v: string) => void;
  mode: TenderMode;
  setMode: (m: TenderMode) => void;
  storeCreditInput: string;
  setStoreCreditInput: (v: string) => void;
  mixedRemainder: MixedRemainderMethod;
  setMixedRemainder: (v: MixedRemainderMethod) => void;
  taiwanPayConfirmed: boolean;
  setTaiwanPayConfirmed: (v: boolean) => void;
  receivedInput: string;
  setReceivedInput: (v: string) => void;
  disabled?: boolean;
}) {
  const plan = resolvePlan(
    mode,
    total,
    parseNtd(storeCreditInput) ?? 0,
    mixedRemainder,
  );
  const validation = validatePlan(plan, total, {
    hasMember,
    memberBalance,
    drawerOpen,
    storeCreditMax,
    storeCreditMinSpend,
    cartHasItems,
    linePayKey,
    taiwanPayConfirmed,
  });
  const received = parseNtd(receivedInput);
  const change = received !== null ? changeDue(received, plan.cash) : null;
  const taiwanPayFee = roundNtdByRate(plan.taiwanPay, taiwanpayFeePct);
  const linePayFee = roundNtdByRate(plan.linePay, linepayFeePct);
  const maxStoreCredit = Math.max(
    0,
    Math.min(total - 1, storeCreditMax, memberBalance ?? 0),
  );
  return (
    <div className="pos-tender">
      <div className="pos-tender-modes" role="radiogroup" aria-label="收款方式">
        {(
          [
            "CASH",
            "STORE_CREDIT",
            "TAIWAN_PAY",
            ...(linepayEnabled ? (["LINE_PAY"] as const) : []),
            "MIXED",
          ] as const
        ).map((m) => (
          <label
            key={m}
            className={`pos-tender-mode ${mode === m ? "is-active" : ""}`}
          >
            <input
              type="radio"
              name="tender-mode"
              checked={mode === m}
              onChange={() => setMode(m)}
              disabled={disabled}
            />
            {m === "CASH"
              ? "現金"
              : m === "STORE_CREDIT"
                ? "購物金"
                : m === "TAIWAN_PAY"
                  ? "台灣Pay"
                  : m === "LINE_PAY"
                    ? "LINE Pay"
                    : "購物金＋其他付款"}
          </label>
        ))}
      </div>

      {mode === "MIXED" && (
        <div className="pos-mixed-panel">
          <div className="pos-mixed-input-row">
            <label className="field">
              <span className="field-label">本次使用購物金</span>
              <input
                value={storeCreditInput}
                onChange={(e) => setStoreCreditInput(e.target.value)}
                inputMode="numeric"
                disabled={disabled}
              />
            </label>
            <button
              type="button"
              className="btn-ghost pos-use-max-credit"
              disabled={disabled || maxStoreCredit <= 0}
              onClick={() => setStoreCreditInput(String(maxStoreCredit))}
            >
              使用可用上限
            </button>
          </div>
          <div className="pos-payment-split" aria-label="付款金額拆分">
            <span>
              購物金 <Money value={Math.max(0, plan.storeCredit)} />
            </span>
            <span>
              剩餘應付{" "}
              <Money
                value={Math.max(0, plan.cash + plan.linePay + plan.taiwanPay)}
              />
            </span>
          </div>
          <div
            className="pos-mixed-methods"
            role="radiogroup"
            aria-label="剩餘款項付款方式"
          >
            {(
              [
                "CASH",
                ...(linepayEnabled ? (["LINE_PAY"] as const) : []),
                "TAIWAN_PAY",
              ] as const
            ).map((method) => (
              <label
                key={method}
                className={`pos-mixed-method ${mixedRemainder === method ? "is-active" : ""}`}
              >
                <input
                  type="radio"
                  name="mixed-remainder-method"
                  checked={mixedRemainder === method}
                  onChange={() => setMixedRemainder(method)}
                  disabled={disabled}
                />
                {method === "CASH"
                  ? "現金"
                  : method === "LINE_PAY"
                    ? "LINE Pay"
                    : "台灣Pay"}
              </label>
            ))}
          </div>
        </div>
      )}
      {plan.storeCredit > 0 && (
        <p className="hint">
          購物金扣抵 <Money value={plan.storeCredit} />
          {memberBalance !== null && (
            <>
              {" "}
              · 餘額 <Money value={memberBalance} />
            </>
          )}
        </p>
      )}
      {plan.taiwanPay > 0 && (
        <>
          <p className="hint">
            台灣Pay 收款 <Money value={plan.taiwanPay} />（請於台灣Pay App 完成收款）
            {taiwanPayFee !== null && /[1-9]/.test(taiwanpayFeePct) && (
              <>
                {" "}
                · 本筆手續費{" "}
                <Money value={taiwanPayFee} />
                （店家負擔，不向客人收取）
              </>
            )}
          </p>
          <label className="field-toggle pos-payment-confirm">
            <input
              type="checkbox"
              checked={taiwanPayConfirmed}
              onChange={(e) => setTaiwanPayConfirmed(e.target.checked)}
              disabled={disabled}
            />
            <span>
              已於台灣Pay收到 <Money value={plan.taiwanPay} />
            </span>
          </label>
        </>
      )}
      {plan.linePay > 0 && (
        <>
          <label className="field">
            <span className="field-label">
              掃描客人 LINE Pay 付款條碼（我的條碼）
            </span>
            <input
              name="linepay_one_time_key"
              value={linePayKey}
              onChange={(e) => setLinePayKey(e.target.value)}
              placeholder="以掃描槍讀取，或手動輸入付款碼數字"
              autoComplete="off"
              disabled={disabled}
            />
          </label>
          <p className="hint">
            LINE Pay 收款 <Money value={plan.linePay} />
            {linePayFee !== null && /[1-9]/.test(linepayFeePct) && (
              <>
                {" "}
                · 本筆手續費{" "}
                <Money value={linePayFee} />
                （店家負擔，不向客人收取）
              </>
            )}
          </p>
        </>
      )}
      {plan.cash > 0 && (
        <label className="field">
          <span className="field-label">實收現金（找零輔助，不影響入帳）</span>
          <input
            value={receivedInput}
            onChange={(e) => setReceivedInput(e.target.value)}
            inputMode="numeric"
            disabled={disabled}
          />
        </label>
      )}
      {change !== null && change >= 0 && (
        <p className="pos-change">
          找零 <Money value={change} />
        </p>
      )}
      {validation.error !== null && (
        <p role="alert" className="form-error">
          {validation.error}
        </p>
      )}
    </div>
  );
}

// ── 完成後：列印商品明細對話框 ──
export interface CompletedSignature {
  // 結帳綁定的扣抵簽署快照（docs/23 K6）：明細聯加印折抵/剩餘＋簽名影像。
  taskId: number;
  deducted: string;
  remaining: string;
}

function PrintDialog({
  sale,
  campaignName,
  signature,
  onClose,
}: {
  sale: SaleRead;
  campaignName: string | null;
  signature: CompletedSignature | null;
  onClose: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [printed, setPrinted] = useState(false);
  const print = useMutation({
    mutationFn: async () => {
      // 1) 實體列印：把 SaleRead（含折扣留痕）轉送硬體代理 → EPSON 印明細聯；
      //    用了購物金且客人簽了扣抵確認 → 加印折抵/剩餘與簽名影像（docs/23 K6）。
      const extras =
        signature !== null
          ? {
              storeCreditDeducted: signature.deducted,
              storeCreditRemaining: signature.remaining,
              signaturePngBase64: await fetchSignaturePngBase64(signature.taskId),
            }
          : undefined;
      await printSaleDetail(sale, campaignName, extras);
      // 2) 列印成功後補稽核（後端記錄補印明細）；稽核失敗不影響已印出的事實。
      await api.POST("/api/v1/sales/{sale_id}/print-detail", {
        params: { path: { sale_id: sale.id } },
      });
    },
    onSuccess: () => {
      setError(null);
      setPrinted(true);
    },
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="列印商品明細"
    >
      <div className="card pos-dialog">
        <h2>列印商品明細？</h2>
        <p className="hint">
          {sale.payment_method === "LINE_PAY" ? "LINE Pay 收款成功。" : "完成結帳。"}
          可現在列印商品明細聯，或日後在交易紀錄補印。
        </p>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        {printed && <p className="form-success">已送出列印。</p>}
        <div className="pos-dialog-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={() => print.mutate()}
            disabled={print.isPending}
          >
            {print.isPending ? "列印中…" : printed ? "再印一次" : "列印明細"}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            {printed ? "完成" : "不用，完成"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface KitchenTicketState {
  /** 這則狀態屬於哪一筆銷售——慢回應與換單的唯一判準。 */
  saleId: number;
  mode: ServiceMode;
  tableNo: string | null;
  outcome: "PENDING" | "SENT" | "SKIPPED" | "FAILED";
  error: string | null;
}

/** 出餐單提示裡的目標描述：內用帶桌號、外帶不帶。 */
function describeKitchenTarget(kitchen: {
  mode: ServiceMode;
  tableNo: string | null;
}): string {
  return kitchen.mode === "DINE_IN" ? `內用 桌號 ${kitchen.tableNo ?? ""}` : "外帶";
}

// -- 生效活動橫幅（純顯示，不算折扣） --
function ActiveCampaignBanner() {
  const query = useQuery({
    queryKey: ["campaigns", "ACTIVE"],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/campaigns", {
        params: { query: { status: "ACTIVE" } },
      });
      return (data ?? []) as CampaignRead[];
    },
    refetchInterval: 60_000, // 每分鐘刷新一次
  });

  const active = query.data ?? [];
  if (active.length === 0) return null;

  return (
    <div className="pos-campaign-banner" role="status">
      {active.map((c) => (
        <span key={c.id} className="pos-campaign-tag">
          活動進行中：{c.name}（{discountDisplay(c.discount_pct)}／折扣 {c.discount_pct}%）
        </span>
      ))}
      <span className="pos-campaign-hint">結帳會自動套用折扣</span>
    </div>
  );
}

// 餐飲數量彈窗：點磚後輸入數量（預設 1，可取消），確認後加入購物車。
function QuantityDialog({
  item,
  onAdd,
  onCancel,
}: {
  item: MenuItemRead;
  onAdd: (qty: number) => void;
  onCancel: () => void;
}) {
  const [qty, setQty] = useState("1");
  const price = parseNtd(item.unit_price) ?? 0;
  const n = Math.max(1, Math.trunc(parseNtd(qty) ?? 1));
  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={`加入 ${item.name}`}
    >
      <div className="card pos-qty-dialog">
        <h2>{item.name}</h2>
        <p className="pos-qty-dialog-price">
          單價 <Money value={price} />
        </p>
        <label className="field">
          <span className="field-label">數量</span>
          <input
            className="pos-qty"
            inputMode="numeric"
            autoFocus
            value={qty}
            aria-label="數量"
            onChange={(e) => setQty(e.target.value)}
          />
        </label>
        <p className="pos-qty-dialog-subtotal">
          小計 <Money value={price * n} />
        </p>
        <div className="pos-dialog-actions">
          <button type="button" className="btn-ghost" onClick={onCancel}>
            取消
          </button>
          <button type="button" className="btn-primary" onClick={() => onAdd(n)}>
            加入購物車
          </button>
        </div>
      </div>
    </div>
  );
}

// 贈品對話框：選原因（必要時填備註）→ 該列改為贈品（成交 0 元，但照樣出庫）。
// 送東西一定要說明為什麼——沒有主管核准機制，原因與備註是事後唯一能追的東西。
function GiftDialog({
  lineDescription,
  onConfirm,
  onCancel,
}: {
  lineDescription: string;
  onConfirm: (reasonId: number, note: string) => void;
  onCancel: () => void;
}) {
  const [reasonId, setReasonId] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["gift-reasons"],
    queryFn: async () => {
      const { data, error: err } = await api.GET("/api/v1/gift-reasons");
      if (!data) throw new Error(extractDetail(err) ?? "讀取贈送原因失敗");
      return data;
    },
  });
  const reasons = query.data ?? [];
  const chosen = reasons.find((r) => r.id === reasonId) ?? null;

  function confirm() {
    if (reasonId === null) {
      setError("請選擇贈送原因");
      return;
    }
    if (chosen?.requires_note && note.trim() === "") {
      setError(`「${chosen.name}」必須填寫備註`);
      return;
    }
    onConfirm(reasonId, note.trim());
  }

  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="改為贈品"
    >
      <div className="card pos-dialog">
        <h2>改為贈品</h2>
        <p className="hint">
          {lineDescription} 將以 NT$0 成交，但仍會扣庫存並列入贈品報表。
        </p>
        {query.isError && (
          <p role="alert" className="form-error">
            {(query.error as Error).message}
          </p>
        )}
        {query.isSuccess && reasons.length === 0 && (
          <p role="alert" className="form-error">
            尚未建立贈送原因，請先到設定頁新增。
          </p>
        )}
        <label className="field">
          <span>贈送原因</span>
          <select
            value={reasonId ?? ""}
            onChange={(e) =>
              setReasonId(e.target.value === "" ? null : Number(e.target.value))
            }
          >
            <option value="">請選擇</option>
            {reasons.map((reason) => (
              <option key={reason.id} value={reason.id}>
                {reason.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>備註{chosen?.requires_note ? "（必填）" : "（選填）"}</span>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={200}
          />
        </label>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="button" className="btn-primary" onClick={confirm}>
            確認贈送
          </button>
          <button type="button" className="btn-ghost" onClick={onCancel}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// 折扣對話框：固定金額或百分比 × 整單或單品。金額預覽一律來自後端試算（加入後即重算）。
function DiscountDialog({
  scopeLabel,
  onConfirm,
  onCancel,
}: {
  scopeLabel: string;
  onConfirm: (draft: {
    method: "FIXED_AMOUNT" | "PERCENTAGE";
    value: number;
    reasonId: number | null;
    note: string | null;
  }) => void;
  onCancel: () => void;
}) {
  const [method, setMethod] = useState<"FIXED_AMOUNT" | "PERCENTAGE">(
    "FIXED_AMOUNT",
  );
  const [value, setValue] = useState("");
  const [reasonId, setReasonId] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["discount-reasons"],
    queryFn: async () => {
      const { data, error: err } = await api.GET("/api/v1/discount-reasons");
      if (!data) throw new Error(extractDetail(err) ?? "讀取折扣原因失敗");
      return data;
    },
  });
  const reasons = query.data ?? [];
  const chosen = reasons.find((r) => r.id === reasonId) ?? null;

  function confirm() {
    const amount = parseNtd(value);
    if (amount === null || amount <= 0) {
      setError("請輸入大於 0 的數字");
      return;
    }
    if (method === "PERCENTAGE" && amount >= 100) {
      setError("折扣百分比必須小於 100；要免費請改用贈品");
      return;
    }
    if (chosen?.requires_note && note.trim() === "") {
      setError(`「${chosen.name}」必須填寫備註`);
      return;
    }
    onConfirm({
      method,
      value: amount,
      reasonId,
      note: note.trim() === "" ? null : note.trim(),
    });
  }

  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="新增折扣"
    >
      <div className="card pos-dialog">
        <h2>{scopeLabel}</h2>
        <div className="pos-discount-methods">
          <label>
            <input
              type="radio"
              name="discount-method"
              checked={method === "FIXED_AMOUNT"}
              onChange={() => setMethod("FIXED_AMOUNT")}
            />
            折抵金額（元）
          </label>
          <label>
            <input
              type="radio"
              name="discount-method"
              checked={method === "PERCENTAGE"}
              onChange={() => setMethod("PERCENTAGE")}
            />
            打折（%）
          </label>
        </div>
        <label className="field">
          <span>{method === "FIXED_AMOUNT" ? "折抵金額" : "折扣百分比"}</span>
          <input
            inputMode="numeric"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-label="折扣數值"
          />
        </label>
        <label className="field">
          <span>折扣原因（選填）</span>
          <select
            value={reasonId ?? ""}
            onChange={(e) =>
              setReasonId(e.target.value === "" ? null : Number(e.target.value))
            }
          >
            <option value="">不指定</option>
            {reasons.map((reason) => (
              <option key={reason.id} value={reason.id}>
                {reason.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>備註{chosen?.requires_note ? "（必填）" : "（選填）"}</span>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={200}
          />
        </label>
        <p className="hint">折後金額由系統重新試算後顯示於右側金額摘要。</p>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="button" className="btn-primary" onClick={confirm}>
            套用折扣
          </button>
          <button type="button" className="btn-ghost" onClick={onCancel}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// 餐飲菜單磚：可售品項一格一格圓角方塊；點磚開數量彈窗。
function MenuPanel({
  onAdd,
  disabled = false,
}: {
  onAdd: (line: CartLine) => void;
  disabled?: boolean;
}) {
  const [selected, setSelected] = useState<MenuItemRead | null>(null);
  const query = useQuery({
    queryKey: ["menu-items", "available"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/menu-items", {
        params: { query: { available_only: true } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取菜單失敗");
      return data;
    },
  });
  const items = query.data ?? [];
  if (items.length === 0) return null;

  function add(qty: number) {
    if (selected === null) return;
    onAdd({
      key: `MENU-${selected.id}`,
      lineType: "MENU",
      description: selected.name,
      unitPrice: parseNtd(selected.unit_price) ?? 0,
      qty,
      menuItemId: selected.id,
    });
    setSelected(null);
  }

  return (
    <div className="pos-menu">
      <h2 className="pos-menu-title">餐飲菜單</h2>
      <div className="pos-menu-tiles">
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            className="pos-menu-tile"
            onClick={() => setSelected(item)}
            disabled={disabled}
          >
            <span className="pos-menu-tile-name">{item.name}</span>
            <span className="pos-menu-tile-price">
              <Money value={parseNtd(item.unit_price) ?? 0} />
            </span>
          </button>
        ))}
      </div>
      {selected !== null && (
        <QuantityDialog item={selected} onAdd={add} onCancel={() => setSelected(null)} />
      )}
    </div>
  );
}

// 區分「紙沒出來」與「紙出來了但沒記到」——兩者要對店員講完全相反的話。
// **必須放模組層**：定義在 component 內每次 render 都是新的 class identity，
// re-render 後 onError 的 instanceof 會比不中。
class ProofRecordError extends Error {}

export default function PosPage() {
  const queryClient = useQueryClient();
  const [lines, setLines] = useState<CartLine[]>([]);
  // 臨時折扣以購物車列的 key 記錄（不是索引）：移除商品時索引會位移，折扣會默默跑到別的商品上。
  const [discountDrafts, setDiscountDrafts] = useState<DiscountDraft[]>([]);
  // 開著的對話框：贈品（要把哪一列改成贈品）／折扣（整單或某一列）。
  const [giftTargetKey, setGiftTargetKey] = useState<string | null>(null);
  const [discountTargetKey, setDiscountTargetKey] = useState<string | null>(null);
  const [discountScopeIsOrder, setDiscountScopeIsOrder] = useState(false);
  const [member, setMember] = useState<ContactRead | null>(null);
  const [mode, setMode] = useState<TenderMode>("CASH");
  const [storeCreditInput, setStoreCreditInput] = useState("");
  const [mixedRemainder, setMixedRemainder] =
    useState<MixedRemainderMethod>("CASH");
  const [taiwanPayConfirmed, setTaiwanPayConfirmed] = useState(false);
  const [receivedInput, setReceivedInput] = useState("");
  // 餐飲內用/外帶與桌號（docs/35）：不預設任一邊——預設哪邊都會被慣性按過去，
  // 而桌號錯的代價是東西送錯桌。
  // 購物車還有變更沒同步到伺服器嗎（送簽前必須同步完成）。初始為 true：還沒收到
  // 第一次回報之前，寧可先擋住。
  const [cartSyncDirty, setCartSyncDirty] = useState(true);
  const [dineIn, setDineIn] = useState<DineInSelection>(clearDineIn());
  // LINE Pay 掃到的客人一次性付款碼（docs/30 P3）；結帳成功後清空、不重用。
  const [linePayKey, setLinePayKey] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [completed, setCompleted] = useState<SaleRead | null>(null);
  // 開錢櫃失敗提示（docs/10 §5：交易已成立，代理離線只提示、不可擋流程）。
  const [drawerNotice, setDrawerNotice] = useState<string | null>(null);
  // 出餐單（docs/35）。**三態**：真的送出、因設定關閉而略過、送出失敗——三者不可混為一談，
  // 說「已送出」但其實沒印，店員會以為吧台收到單了。
  // 內用/外帶與桌號一律取自**後端回傳的 sale**，不用送出前的本地快照：sale 才是權威值，
  // 而且補救路徑（回應遺失/付款對帳後補單）根本沒有本地快照可用。
  // 出餐單狀態**自己帶單號**：慢回應回來時才知道自己講的是不是畫面上這一筆。
  // （原本用一個獨立的 ref 當守衛，但 resetSale 清了狀態卻沒清 ref，舊單的失敗會被
  // 誤判成「當前」而寫進一個根本不渲染的狀態——吧台漏單且畫面完全不提示。）
  const [kitchen, setKitchen] = useState<KitchenTicketState | null>(null);
  // `kitchen` 的鏡像：promise 落地時要同步讀，不能等 render。與 setKitchen 一起更新。
  const kitchenSaleId = useRef<number | null>(null);
  const applyKitchen = useCallback((next: KitchenTicketState | null) => {
    kitchenSaleId.current = next?.saleId ?? null;
    setKitchen(next);
  }, []);
  // 遲到的列印失敗（店員已經換下一筆）不能就這樣丟掉——吧台沒收到單，畫面卻什麼都不說。
  // **必須是清單**：印表機卡住時往往連累好幾筆，只留一格會讓先前那幾桌的警告被蓋掉，
  // 那幾單就此無聲無息。每筆記單號，逐筆確認或補印才消掉。
  const [staleKitchen, setStaleKitchen] = useState<{ saleId: number; message: string }[]>(
    [],
  );
  const pushStaleKitchen = useCallback((saleId: number, message: string) => {
    setStaleKitchen((prev) =>
      prev.some((item) => item.saleId === saleId) ? prev : [...prev, { saleId, message }],
    );
  }, []);
  // 結帳當下生效活動名（供明細聯顯示活動）；結帳成功時自試算結果擷取、清單一變即失效不影響。
  const [completedCampaign, setCompletedCampaign] = useState<string | null>(null);
  const [showDialog, setShowDialog] = useState(false);
  // 商品備註結帳提醒（2026-09-02 裁示）：按下結帳先跳出，列出車內所有帶備註的商品，
  // 店員確認後才進收款。noteAck 存的是「已確認的那組備註」的指紋——再掃進一件有備註的
  // 商品時指紋會變，會再問一次，避免後加入的提醒被前一次確認吃掉。
  const [noteAck, setNoteAck] = useState("");
  const [noteDialogOpen, setNoteDialogOpen] = useState(false);
  // 還原是非同步的（要重新取回每件商品的備註）。等待期間店員仍可掃碼，晚回來的結果
  // 會把那些操作整批覆蓋掉；兩次還原重疊時也可能是**較舊**的那次最後寫入。
  // 用遞增世代識別「這次還原還算不算數」，並在還原期間鎖住購物車操作與結帳。
  const restoreGenerationRef = useRef(0);
  // 還原時要知道「現在車上有沒有東西」。restoreCustomerDisplayCart 的相依是 []，
  // 讀不到最新的 lines，用 ref 帶進去。
  const linesRef = useRef<CartLine[]>([]);
  const [restoring, setRestoring] = useState(false);
  // 掛載到「確定沒有東西要還原／還原完成」之間也要鎖：那段期間掃進去的商品會被
  // onRestore 的 setLines 整批覆蓋而無聲消失（同步 effect 也被 hydrated 擋著推不上去）。
  // 初值 true 是為了第一個 frame 就 fail closed；放行由 PosCustomerDisplay 負責，
  // 它帶有硬性期限，不會把收銀台鎖死。
  const [restorePending, setRestorePending] = useState(true);
  // 購物金扣抵手持簽署（docs/23 K5，D3）：推送至手持裝置後的任務 id；輪詢其狀態，
  // SIGNED 後結帳帶 signature_task_id 綁定（後端驗折抵額精確相符＋單次使用）。
  const [signTaskId, setSignTaskId] = useState<number | null>(null);
  const [displayTerminal, setDisplayTerminal] = useState<TerminalRead | null>(null);
  const [displayCart, setDisplayCart] = useState<DisplayCart | null>(null);
  const [reconcileReason, setReconcileReason] = useState("");
  const [reconcileEvidenceType, setReconcileEvidenceType] = useState("");
  const [reconcileEvidenceReference, setReconcileEvidenceReference] = useState("");
  // 完成結帳時綁定的簽署快照（K6 明細聯加印折抵/剩餘＋簽名用；未綁定為 null）。
  const [completedSignature, setCompletedSignature] = useState<CompletedSignature | null>(null);
  const isManager = decodeSession()?.role === "MANAGER";

  const restoreCustomerDisplayCart = useCallback(
    async (cart: components["schemas"]["StaffCartSessionRead"]) => {
      // 優先用店員端保存的原始請求還原：客顯快照沒有贈品原因、備註與折扣意圖，
      // 只靠它重建會把贈品與折扣整個弄丟，接著同步 effect 又會把殘缺狀態寫回伺服器。
      const payload = cart.staff_payload ?? null;
      const generation = restoreGenerationRef.current + 1;
      restoreGenerationRef.current = generation;
      const isStale = () => restoreGenerationRef.current !== generation;
      setRestoring(true);
      // 還原＝換了一份購物車，先前對備註的「已確認」一律失效，必須重新確認。
      setNoteAck("");
      setNoteDialogOpen(false);
      // 還原會整批取代購物車。車上原本有東西就是被蓋掉了——**不能無聲**，那正是這條
      // 修正要防的事。（會走到這裡而非作廢，代表伺服器購物車是不可覆寫的狀態：
      // 簽署中／付款處理中／付款待確認，此時本地當不成權威，只能以伺服器為準。）
      const overwrote = linesRef.current.length > 0;
      try {
      if (payload) {
        const restoredLines: CartLine[] = payload.lines.map((line, index) => {
            const gift = line.line_kind === "GIFT";
            const snapshot = cart.snapshot.items[index];
            const base =
              line.line_type === "SERIALIZED"
                ? `S:${line.item_code}`
                : line.line_type === "CATALOG"
                  ? `C:${line.catalog_product_id}`
                  : line.line_type === "BULK_LOT"
                    ? `B:${line.bulk_lot_id}`
                    : `MENU-${line.menu_item_id}`;
            return {
              key: gift ? `G:${base}` : base,
              lineType: line.line_type,
              description: snapshot?.name ?? "",
              unitPrice: parseNtd(snapshot?.unit_price ?? "0") ?? 0,
              qty: line.qty,
              itemCode: line.item_code ?? undefined,
              catalogProductId: line.catalog_product_id ?? undefined,
              bulkLotId: line.bulk_lot_id ?? undefined,
              menuItemId: line.menu_item_id ?? undefined,
              lineKind: gift ? "GIFT" : "NORMAL",
              giftReasonId: line.gift_reason_id ?? undefined,
              giftNote: line.gift_note ?? undefined,
            };
        });
        const withNotes = await withFreshNotes(restoredLines);
        if (isStale()) return;
        setLines(withNotes);
        setDiscountDrafts(
          (payload.adjustments ?? []).map((adjustment, index) => ({
            id: `restored-${index}`,
            scope: adjustment.scope,
            targetKey:
              adjustment.target_line_index == null
                ? null
                : (() => {
                    const line = payload.lines[adjustment.target_line_index];
                    if (!line) return null;
                    const base =
                      line.line_type === "SERIALIZED"
                        ? `S:${line.item_code}`
                        : line.line_type === "CATALOG"
                          ? `C:${line.catalog_product_id}`
                          : line.line_type === "BULK_LOT"
                            ? `B:${line.bulk_lot_id}`
                            : `MENU-${line.menu_item_id}`;
                    return line.line_kind === "GIFT" ? `G:${base}` : base;
                  })(),
            method: adjustment.method,
            value: parseNtd(adjustment.value) ?? 0,
            reasonId: adjustment.reason_id ?? null,
            note: adjustment.note ?? null,
          })),
        );
      } else {
        // 升級前建立的舊購物車沒有這份資料：只能以快照重建（贈品原因與折扣無從得知）。
        const withNotes = await withFreshNotes(restoreLines(cart.snapshot.items));
        if (isStale()) return;
        setLines(withNotes);
        setDiscountDrafts([]);
      }
      // 內用/外帶與桌號（docs/35）：一併還原。少了它，被凍結的餐飲購物車重掛後會卡死——
      // 驗證要求選擇，但凍結中兩顆模式鍵都是停用的，只能作廢重簽。
      setDineIn({
        mode: payload?.service_mode ?? null,
        tableNo: payload?.table_no ?? null,
      });
      setSignTaskId(cart.active_signature_task_id ?? null);
      const storeCredit = cart.snapshot.tenders.find(
        (tender) => tender.tender_type === "STORE_CREDIT",
      );
      const remainder = cart.snapshot.tenders.find(
        (tender) => tender.tender_type !== "STORE_CREDIT",
      );
      if (storeCredit) {
        setStoreCreditInput(storeCredit.amount);
        if (remainder) {
          setMode("MIXED");
          setMixedRemainder(remainder.tender_type as MixedRemainderMethod);
        } else {
          setMode("STORE_CREDIT");
        }
      } else if (remainder) {
        setMode(remainder.tender_type as TenderMode);
      }
      setReceivedInput(
        remainder?.tender_type === "CASH" ? remainder.amount : "",
      );
      setLinePayKey("");
      setTaiwanPayConfirmed(false);
      // 會員查詢留在鎖內，但**加上期限**。
      // 移到鎖外雖然能防卡死，卻開出一個窗口：解鎖後店員可能已改選別人或開始結帳，
      // 晚到的結果會覆寫他的選擇，甚至送出 buyer_contact_id=null 與伺服器購物車不符。
      // 有了期限，鎖內等待是有界的（後端在同一台機器上，正常是毫秒級），
      // 既沒有窗口也不會卡死；逾時就放棄帶入，店員可自行重查。
      if (cart.buyer_contact_id !== null) {
        const controller = new AbortController();
        const result = await withDeadline(
          api.GET("/api/v1/contacts/{contact_id}", {
            params: { path: { contact_id: cart.buyer_contact_id } },
            signal: controller.signal,
          }),
          RESTORE_LOOKUP_TIMEOUT_MS,
          null,
          () => controller.abort(),
        );
        if (isStale()) return;
        setMember(result?.data ?? null);
      } else {
        setMember(null);
      }
      } finally {
        // 中途拋例外也一定要解鎖，否則收銀台會永遠結不了帳——那比漏提醒更糟。
        // 被更新的還原接手時，由新的那次負責解鎖。
        if (!isStale()) setRestoring(false);
      }
      if (overwrote && !isStale()) {
        setNotice(
          "已改用顧客螢幕上那筆未完成的購物車；剛才掃入的商品沒有被保留，請確認後重新掃描。",
        );
      }
    },
    [],
  );

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/settings");
      if (!data) throw new Error(extractDetail(error) ?? "讀取設定失敗");
      return data;
    },
  });
  const einvoiceEnabled = settings.data?.einvoice_enabled ?? false;
  // 出餐單開關（docs/35）：設定讀不到時視為關閉——寧可不印也不要在店家沒開這功能時噴紙。
  const printKitchenEnabled = settings.data?.print_kitchen_ticket ?? false;

  // 電子發票（docs/24）：統編（=B2B）/手機載具/捐贈碼三擇一；結帳成功後自動開立，
  // 無載具且未捐贈 → 以 Amego 回傳條碼/QR 內容送 EPSON 印證明聯。
  const [invTaxId, setInvTaxId] = useState("");
  const [invBuyerName, setInvBuyerName] = useState("");
  const [invCarrier, setInvCarrier] = useState("");
  const [invNpoban, setInvNpoban] = useState("");
  const [invoiceNote, setInvoiceNote] = useState<string | null>(null);
  const [completedInvoice, setCompletedInvoice] = useState<InvoiceRead | null>(null);
  const invTaxIdBad = invTaxId !== "" && !/^\d{8}$/.test(invTaxId);
  const invCarrierBad = invCarrier !== "" && !/^\/[0-9A-Z+\-.]{7}$/.test(invCarrier);
  const invNpobanBad = invNpoban !== "" && !/^\d{3,7}$/.test(invNpoban);
  const invoiceInputBad = invTaxIdBad || invCarrierBad || invNpobanBad;

  // 證明聯抬頭（賣方統編/店名）＝後端 stores 單一事實來源（與明細聯同源）。
  const storeHeader = useQuery({
    queryKey: ["receipt-header"],
    enabled: einvoiceEnabled,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/stores/{store_id}/receipt-header", {
        params: { path: { store_id: decodeSession()?.storeId ?? 1 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取店家抬頭失敗");
      return data;
    },
  });

  // 證明聯列印（獨立 mutation，Codex 第十六輪）：發票已開立但列印失敗（代理離線/缺紙/
  // 抬頭未載入）時，完成畫面提供「重印證明聯」重試——不可只留一行提示無路可退。
  // 出餐單（docs/35）：有餐飲行且設定開啟才印，**不詢問**——這是內部作業單，每一筆都要
  // （與「商品明細」不同，那個是問客人要不要）。fire-and-forget：交易已寫後端，代理離線
  // 只提示、不擋流程；但提示要明顯且可重印，吧台沒拿到單就不會做東西。
  // 正常完成與**補救完成**（回應遺失、付款對帳後補單）共用這一支，否則補救出來的餐飲單
  // 不會自動出單、完成頁也沒有重印鍵。
  const startKitchenTicket = useCallback(
    (sale: SaleRead, enabled: boolean) => {
      // 代理端若卡在死掉的印表機，這個 promise 可能拖上數十秒；那段期間店員已經結完
      // 下一筆了。回來的失敗必須認得出自己已經過期，否則會把**別桌**的錯誤蓋到現在
      // 這張完成頁上（而重印鍵印的又是現在這筆）。以單號認身分。
      const mode = sale.service_mode;
      if (mode == null) {
        applyKitchen(null);
        return;
      }
      const tableNo = sale.table_no ?? null;
      const base = { saleId: sale.id, mode, tableNo };
      if (!enabled) {
        applyKitchen({ ...base, outcome: "SKIPPED", error: null });
        return;
      }
      // **不可在 promise 落地前就說「已送出」**：印表機卡住時會一直顯示成功，
      // 而吧台其實沒收到單。先 PENDING，等結果出來才定案。
      applyKitchen({ ...base, outcome: "PENDING", error: null });
      printKitchenTicket(sale, mode, tableNo).then(
        () => {
          if (kitchenSaleId.current !== sale.id) return;
          applyKitchen({ ...base, outcome: "SENT", error: null });
        },
        (err: Error) => {
          if (kitchenSaleId.current !== sale.id) {
            // 已換單，或已按「開始下一筆」清空：這則失敗仍必須讓店員知道，
            // 只是不能蓋到現在這張完成頁上。
            pushStaleKitchen(
              sale.id,
              `#${sale.id} 的出餐單列印失敗：${err.message}（吧台未收到該單，請至交易紀錄重印）`,
            );
            return;
          }
          applyKitchen({ ...base, outcome: "FAILED", error: err.message });
        },
      );
    },
    [applyKitchen, pushStaleKitchen],
  );

  // 出餐單重印（docs/35）：代理離線/缺紙時第一次會失敗，而交易已成立不會進 error 態
  // ——吧台沒拿到單就不會做東西，必須有在地重試入口。
  const reprintKitchen = useMutation({
    mutationFn: async ({ sale }: { sale: SaleRead }) => {
      if (sale.service_mode == null) {
        throw new Error("本單沒有餐飲品項，無需出餐單");
      }
      await printKitchenTicket(sale, sale.service_mode, sale.table_no ?? null);
      return { saleId: sale.id, mode: sale.service_mode, tableNo: sale.table_no ?? null };
    },
    onSuccess: ({ saleId, mode, tableNo }) => {
      // 只清掉**同一筆**的遲到警告：補印 B 不代表 A 印出來了。
      setStaleKitchen((prev) => prev.filter((item) => item.saleId !== saleId));
      if (kitchenSaleId.current !== saleId) return;  // 已換單，過期結果不覆蓋
      applyKitchen({ saleId, mode, tableNo, outcome: "SENT", error: null });
    },
    // 失敗路徑同樣要認身分：`reset()` 不會取消在途的 mutation，A 單的慢失敗會把
    // 現在這張（B 單）完成頁改寫成 FAILED，而重印鍵印的是 B。
    onError: (err: Error, { sale }) => {
      if (kitchenSaleId.current !== sale.id) {
        pushStaleKitchen(
          sale.id,
          `#${sale.id} 的出餐單列印失敗：${err.message}（吧台未收到該單，請至交易紀錄重印）`,
        );
        return;
      }
      setKitchen((prev) =>
        prev === null ? prev : { ...prev, outcome: "FAILED", error: err.message },
      );
    },
  });

  const printProof = useMutation({
    mutationFn: async ({ invoice, sale }: { invoice: InvoiceRead; sale: SaleRead }) => {
      // 抬頭：優先用查詢快取；未就緒/曾失敗 → 即時補抓一次，不因慢載入放棄列印。
      let header = storeHeader.data;
      if (header == null) header = (await storeHeader.refetch()).data ?? undefined;
      if (header?.tax_id == null) throw new Error("讀不到店家統編抬頭");
      await printEInvoice(invoice, sale, { taxId: header.tax_id, name: header.name });
      // 印出來了才記——這是「證明聯以列印一次為限」的那一次。沒記到的話，
      // 之後在交易紀錄按補印會誤判成正本而多印一張；記錯邊則是印成補印，
      // 客人拿到一張須併同原聯才能兌獎的紙。
      //
      // **紙已經出來了，所以記錄失敗不能說成「列印失敗」**：那句話會叫店員再按一次重印，
      // 而未記錄狀態下的重印會印出**第二張正本**——同一張發票兩張正本，
      // 重複兌領的責任在營業人身上。這裡回報的是記錄失敗，並明講不要再印。
      const { error } = await api.POST("/api/v1/einvoice/sales/{sale_id}/proof-printed", {
        params: { path: { sale_id: sale.id } },
      });
      if (error) throw new ProofRecordError(extractDetail(error) ?? "記錄失敗");
    },
    onSuccess: () => setInvoiceNote("發票已開立，證明聯已送印"),
    onError: (err: Error) =>
      setInvoiceNote(
        err instanceof ProofRecordError
          ? `證明聯已印出，但系統沒記錄到（${err.message}）。請勿重印，重印會多出一張正本。`
          : `發票已開立，但證明聯列印失敗：${err.message}（可按重印）`,
      ),
  });

  // 結帳後開立（docs/24）：失敗不擋交易（銷售已成立），留待補開清單重試。
  const issueInvoice = useMutation({
    mutationFn: async (sale: SaleRead): Promise<{ invoice: InvoiceRead; sale: SaleRead }> => {
      const { data, error } = await api.POST("/api/v1/einvoice/sales/{sale_id}/issue", {
        params: { path: { sale_id: sale.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "發票開立失敗");
      return { invoice: data, sale };
    },
    onSuccess: ({ invoice, sale }) => {
      setCompletedInvoice(invoice);
      if (invoiceProofPrintable(invoice)) {
        setInvoiceNote("證明聯列印中…");
        printProof.mutate({ invoice, sale });
      } else if (invoice.donate_mark) {
        setInvoiceNote("發票已開立並捐贈，不印證明聯");
      } else if (invoice.issue_channel === "MANUAL_PAPER") {
        // 手開紙本（docs/36）：另一台終端可能已登記手開發票。這張**平台上不存在也沒有
        // 條碼**，導引去光貿補印只會讓店員撲空（Codex 對抗審查第八輪 medium）。
        setInvoiceNote(
          `本筆已登記手開紙本發票 ${invoice.invoice_no ?? ""}（紙本已交給客人），` +
            "不需要也無法列印電子證明聯",
        );
      } else if (invoice.carrier_type != null) {
        setInvoiceNote("發票已開立並存入載具，不印證明聯");
      } else {
        // 復原件（前次連線中斷、以平台查詢補開立）：條碼/QR 內容平台查詢不回傳，
        // 本機無法印合規證明聯（QR 需 Amego 端 AES）——明確導引人工補印（Codex 第十七輪）。
        setInvoiceNote(
          "發票已開立（連線中斷後復原），證明聯內容未能取回——請至光貿後台" +
            "（invoice.amego.tw）補印或由客人以載具歸戶",
        );
      }
    },
    onError: (err: Error) => {
      // 錯誤原文留著給維護者判讀，但**店員需要的是下一步**：字軌用完/平台故障正是
      // 手開紙本備用發票的使用時機（docs/36），畫面若只丟一句平台錯誤碼，店員會卡在
      // 這裡不知道能做什麼。
      setInvoiceNote(
        `發票尚未開立：${err.message}（銷售已成立，可稍後補開；` +
          "若平台持續失敗或字軌已用完，請改開紙本發票給客人，並至「交易紀錄」登記）",
      );
    },
  });
  const balanceQuery = useQuery({
    queryKey: ["store-credit", member?.id],
    enabled: member !== null,
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/contacts/{contact_id}/store-credit",
        {
          params: { path: { contact_id: member!.id } },
        },
      );
      if (!data) throw new Error(extractDetail(error) ?? "讀取餘額失敗");
      return data;
    },
  });
  // 開帳狀態（含現金收款必須開帳，§7.8）：200 回 session|null。
  const cashSession = useQuery({
    queryKey: ["cash-session", "current"],
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/v1/cash-sessions/current",
      );
      if (response.status === 200) return data ?? null;
      throw new Error(extractDetail(error) ?? "讀取開帳狀態失敗");
    },
  });

  const saleLines = toSaleLines(lines);
  // 目標商品已被移除的折扣自動失效；送出時才把 key 換算成後端要的明細索引。
  const activeDiscounts = pruneDiscounts(discountDrafts, lines);
  const adjustments = toAdjustmentRequests(activeDiscounts, lines);
  // 結帳前向後端試算折後總額（docs/21 C2b）：活動生效時 total=折後，收款據此對齊（否則 422）。
  // 贈品與臨時折扣也在此算——畫面上的每個數字都由後端給，前端不自算。
  const quote = useQuery({
    queryKey: [
      "sale-quote",
      JSON.stringify(saleLines),
      JSON.stringify(adjustments),
      member?.id ?? null,
    ],
    enabled: lines.length > 0,
    queryFn: async () => {
      const { data, error } = await api.POST("/api/v1/sales/quote", {
        body: {
          lines: saleLines,
          buyer_contact_id: member?.id ?? null,
          adjustments: adjustments.length > 0 ? adjustments : null,
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "試算失敗");
      return data;
    },
  });
  // 試算就緒：空車視為就緒；否則查詢成功且非重抓中（避免用折前/過期金額結帳）。
  const quoteReady = lines.length === 0 || (quote.isSuccess && !quote.isFetching);
  const quotedTotal = parseNtd(quote.data?.total ?? "") ?? 0;
  // 應付總額：就緒用後端折後 quotedTotal；試算中暫顯折前估計（結帳鍵另以 quoteReady 鎖住）。
  const total = quoteReady && lines.length > 0 ? quotedTotal : cartTotal(lines);
  // 逐行折後（docs/21）：試算就緒時 quote.lines 與購物車同序，逐行顯示折後單價/小計與原價；
  // 試算中（refetch）暫無 → 退回折前估計。
  const quotedLines = quoteReady && quote.data ? quote.data.lines : null;
  const campaignNote = quote.data?.campaign_name ?? null;
  // 金額摘要的三個數字一律取自後端試算，前端不自算（同 total 的既有慣例）。
  const itemDiscountTotal = parseNtd(quote.data?.item_discount_amount ?? "") ?? 0;
  const orderDiscountTotal =
    parseNtd(quote.data?.order_discount_amount ?? "") ?? 0;
  const giftRetailValue = parseNtd(quote.data?.gift_retail_value ?? "") ?? 0;
  const memberBalance =
    member !== null && balanceQuery.data
      ? (parseNtd(balanceQuery.data.balance) ?? 0)
      : null;
  // drawerOpen：讀取中/失敗 → null（未知，含現金收款先擋）；否則為是否有開帳中 session。
  const drawerOpen =
    cashSession.isSuccess === true ? cashSession.data !== null : null;
  // 購物金可折抵上限（內用不得以購物金折抵）：試算回 store_credit_max；無餐飲時=total。
  const storeCreditMax = quote.data
    ? (parseNtd(quote.data.store_credit_max) ?? total)
    : total;
  // 購物金低消門檻（非餐飲消費未達則完全不可用購物金；0＝不限）：試算回 store_credit_min_spend。
  // 欄位缺漏（舊回應）一律視為 0＝不限，避免誤擋。
  const storeCreditMinSpend =
    quote.data?.store_credit_min_spend != null
      ? (parseNtd(quote.data.store_credit_min_spend) ?? 0)
      : 0;
  const plan = resolvePlan(
    mode,
    total,
    parseNtd(storeCreditInput) ?? 0,
    mixedRemainder,
  );
  const allCustomerDisplayTenders: components["schemas"]["CartTenderRequest"][] = [
    { tender_type: "STORE_CREDIT", amount: String(plan.storeCredit) },
    { tender_type: "CASH", amount: String(plan.cash) },
    { tender_type: "LINE_PAY", amount: String(plan.linePay) },
    { tender_type: "TAIWAN_PAY", amount: String(plan.taiwanPay) },
  ];
  const customerDisplayTenders = allCustomerDisplayTenders.filter(
    (tender) => Number(tender.amount) > 0,
  );
  const previousLinePayAmount = useRef(plan.linePay);
  const previousTaiwanPayAmount = useRef(plan.taiwanPay);
  useEffect(() => {
    if (previousLinePayAmount.current !== plan.linePay) setLinePayKey("");
    previousLinePayAmount.current = plan.linePay;
  }, [plan.linePay]);
  useEffect(() => {
    if (previousTaiwanPayAmount.current !== plan.taiwanPay) {
      setTaiwanPayConfirmed(false);
    }
    previousTaiwanPayAmount.current = plan.taiwanPay;
  }, [plan.taiwanPay]);
  const validation = validatePlan(plan, total, {
    hasMember: member !== null,
    memberBalance,
    drawerOpen,
    storeCreditMax,
    storeCreditMinSpend,
    cartHasItems: lines.length > 0,
    linePayKey,
    taiwanPayConfirmed,
  });

  // 餐飲內用/外帶與桌號（docs/35）：只在購物車有餐飲行時登場，與金額完全無關。
  const hasMenuLine = lines.some((line) => line.lineType === "MENU");
  const dineInTables = settings.data?.dine_in_tables ?? [];
  const dineInValidation = validateDineIn(hasMenuLine, dineIn, dineInTables);

  // 商品備註提醒：車內帶備註的行，與「這組備註是否已被確認過」。
  const notedLines = linesWithNotes(lines);
  const noteFingerprint = noteAckFingerprint(lines);
  const noteAckPending = notedLines.length > 0 && noteAck !== noteFingerprint;

  // 購物金扣抵手持簽署（docs/23 K5）：輪詢任務狀態；簽署快照的折抵額須與當前收款計畫相符，
  // 改了購物車/收款即失配 → 顯示警告並要求作廢重推（後端結帳時亦精確比對，雙重防線）。
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
  const signTaskFinishedWithoutSale =
    signTask.data?.status === "VOIDED" ||
    signTask.data?.status === "EXPIRED" ||
    signTask.data?.status === "FAILED";
  const signedDebit =
    signTask.data != null
      ? String(
          (signTask.data.content as Record<string, unknown>).store_credit_amount ?? "",
        )
      : null;
  const signedTotal =
    signTask.data != null
      ? String((signTask.data.content as Record<string, unknown>).total ?? "")
      : null;
  const signedBalanceBefore =
    signTask.data != null
      ? String(
          (signTask.data.content as Record<string, unknown>)
            .store_credit_balance_before ?? "",
        )
      : null;
  const signedBalanceAfter =
    signTask.data != null
      ? String(
          (signTask.data.content as Record<string, unknown>)
            .store_credit_balance_after ?? "",
        )
      : null;
  // 失配＝折抵額/消費合計/餘額快照任一與簽署不符（客人簽的必須就是這筆交易與當下餘額；
  // 後端結帳時亦以帳戶行鎖精確比對——此處為即時 UX 提示）。
  const signMismatch =
    signTaskId != null &&
    (signedDebit !== String(plan.storeCredit) ||
      signedTotal !== String(total) ||
      (memberBalance !== null && signedBalanceBefore !== String(memberBalance)));
  // 購物金一律須由已配對顧客螢幕簽署；政策設定不再能略過這道證據閘門。
  const scSignBlock =
    plan.storeCredit > 0 &&
    (signTaskId == null ||
      !signed ||
      signMismatch ||
      displayCart?.status !== "FROZEN");
  useEffect(() => {
    linesRef.current = lines;
  }, [lines]);

  const cartMutationLocked =
    restoring ||
    restorePending ||
    displayCart?.status === "FROZEN" ||
    displayCart?.status === "PROCESSING" ||
    displayCart?.status === "PAYMENT_UNCERTAIN";

  const pushSign = useMutation({
    mutationFn: async () => {
      if (!member) throw new Error("請先選擇會員");
      let terminal = displayTerminal;
      if (!terminal) {
        const registered = await api.POST("/api/v1/customer-display/terminals", {
          body: {
            installation_id: terminalInstallationId(),
            name: "主要櫃檯",
          },
        });
        terminal = registered.data ?? null;
      }
      if (!terminal?.paired_kiosk) {
        throw new Error("購物金付款必須先配對並連線顧客螢幕");
      }
      if (!terminal.paired_kiosk.online) {
        throw new Error("顧客螢幕目前離線；請改用其他付款方式");
      }
      const current = await api.GET(
        "/api/v1/customer-display/terminals/{terminal_id}/cart/current",
        { params: { path: { terminal_id: terminal.id } } },
      );
      if (!current.data || current.data.status !== "DRAFT") {
        throw new Error("顧客螢幕購物車尚未同步完成，請稍候後再送出簽署");
      }
      const { data, error } = await api.POST(
        "/api/v1/customer-display/terminals/{terminal_id}/cart/freeze-for-signature",
        {
          params: { path: { terminal_id: terminal.id } },
          body: { expected_revision: current.data.revision },
        },
      );
      if (!data) throw new Error(extractDetail(error) ?? "推送手持簽署失敗");
      return data;
    },
    onSuccess: (d) => {
      setNotice(null);
      setDisplayCart(d.cart);
      setSignTaskId(d.signature_task_id);
      // 客顯元件自己有一份購物車查詢快取；不讓它失效的話它仍以為是 DRAFT，
      // 下一次輪詢就會對已凍結的購物車送 PUT、拿到 409 並留下錯誤（Codex 第五輪）。
      void queryClient.invalidateQueries({ queryKey: ["customer-display", "cart"] });
    },
    onError: (e: Error) => setNotice(e.message),
  });
  const cancelSign = useMutation({
    mutationFn: async (cashFallback: boolean) => {
      if (signTaskId == null) return;
      const { response } = await api.POST("/api/v1/signing/tasks/{task_id}/cancel", {
        params: { path: { task_id: signTaskId } },
        body: cashFallback
          ? {
              reason_code: "KIOSK_FAILURE_CASH_FALLBACK",
              reason: "顧客螢幕故障，改用現金",
            }
          : {
              reason_code: "CONTENT_CHANGED",
              reason: "撤回簽署並修改交易內容",
            },
      });
      // 非 2xx（如客人剛好已簽 → 409）不可視為取消成功而清除綁定（同 K4）：重新輪詢取回
      // 最新狀態，保留 signTaskId。SIGNED 在成交前可依規格作廢；只有 CONSUMED 等終態會拒絕。
      if (!response.ok) {
        await signTask.refetch();
        throw new Error("此簽署已進入不可撤回狀態，請確認交易是否已成立");
      }
    },
    onSuccess: async (_data, cashFallback) => {
      setSignTaskId(null);
      if (cashFallback) {
        setMode("CASH");
        setStoreCreditInput("");
        setMixedRemainder("CASH");
        setLinePayKey("");
        setTaiwanPayConfirmed(false);
        setNotice("舊簽署已保留為作廢證據；本筆付款已改為全額現金。");
      }
      await queryClient.invalidateQueries({
        queryKey: ["customer-display", "cart", displayTerminal?.id],
      });
      if (displayTerminal) {
        const { data } = await api.GET(
          "/api/v1/customer-display/terminals/{terminal_id}/cart/current",
          { params: { path: { terminal_id: displayTerminal.id } } },
        );
        setDisplayCart(data ?? null);
      }
    },
    onError: (e: Error) => setNotice(e.message),
  });

  async function showCompletedCart(
    cart: components["schemas"]["StaffCartSessionRead"],
    successNotice: string,
  ): Promise<void> {
    if (cart.sale_id == null) return;
    const { data: sale, error } = await api.GET("/api/v1/sales/{sale_id}", {
      params: { path: { sale_id: cart.sale_id } },
    });
    if (!sale) {
      setNotice(extractDetail(error) ?? "交易已成立，但無法載入完成畫面；請至交易紀錄確認。");
      return;
    }
    clearPersistedIdemKey("pos-checkout");
    setCompleted(sale);
    setCompletedCampaign(cart.snapshot.campaign_name ?? null);
    setCompletedSignature(
      signTaskId != null && signedBalanceAfter != null
        ? {
            taskId: signTaskId,
            deducted: String(plan.storeCredit),
            remaining: signedBalanceAfter,
          }
        : null,
    );
    setCompletedInvoice(null);
    setInvoiceNote(null);
    setShowDialog(true);
    setNotice(successNotice);
    // 補救完成（回應遺失後恢復、或付款對帳後補單）同樣要出餐單並提供重印入口。
    // 設定尚未載入或載入失敗時**不可拿 `?? false` 當成「店家關閉了自動列印」**——
    // 那會讓補救出來的單不印，畫面還謊稱是設定關閉的。比照本檔結帳的做法直接重讀。
    let kitchenEnabled = printKitchenEnabled;
    if (!settings.isSuccess) {
      const fresh = await api.GET("/api/v1/settings");
      if (fresh.data) {
        queryClient.setQueryData(["settings"], fresh.data);
        kitchenEnabled = fresh.data.print_kitchen_ticket;
      }
    }
    startKitchenTicket(sale, kitchenEnabled);
    if (sale.invoice_status === "PENDING_ISSUE") {
      setInvoiceNote("發票開立中…");
      issueInvoice.mutate(sale);
    }
    if (plan.cash > 0) {
      setDrawerNotice(null);
      openCashDrawer().catch((error: Error) => setDrawerNotice(error.message));
    }
  }

  const reconcilePayment = useMutation({
    mutationFn: async (
      action: "QUERY_PROVIDER" | "MANUAL_SUCCESS" | "MANUAL_FAILED",
    ) => {
      if (!displayTerminal) throw new Error("找不到目前 POS 櫃檯");
      const manual = action !== "QUERY_PROVIDER";
      const { data, error } = await api.POST(
        "/api/v1/customer-display/terminals/{terminal_id}/cart/reconcile-payment",
        {
          params: { path: { terminal_id: displayTerminal.id } },
          body: {
            action,
            reason: manual ? reconcileReason.trim() || null : null,
            evidence_type: manual ? reconcileEvidenceType.trim() || null : null,
            evidence_reference: manual
              ? reconcileEvidenceReference.trim() || null
              : null,
          },
        },
      );
      if (!data) throw new Error(extractDetail(error) ?? "付款對帳失敗");
      return data;
    },
    onSuccess: async (data) => {
      setDisplayCart(data.cart);
      if (data.outcome === "STILL_UNCERTAIN") {
        setNotice("LINE Pay 那邊還是查不到結果。請稍後再查一次，或由店長比對客人的付款紀錄後判斷。");
      } else if (data.outcome === "SUCCESS_CONFIRMED") {
        if (data.cart.sale_id == null) {
          setNotice("已確認 LINE Pay 付款成功，但本機交易尚未補成立；請勿重新付款並聯絡管理員。");
        } else {
          await showCompletedCart(
            data.cart,
            "已確認 LINE Pay 付款成功，並自動補成立本機交易。",
          );
        }
      } else {
        setNotice("已確認 LINE Pay 未付款；購物車已解鎖。使用購物金時須重新簽署。");
        setSignTaskId(null);
      }
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const checkout = useMutation({
    mutationFn: async (): Promise<{
      sale: SaleRead;
      sig: CompletedSignature | null;
    }> => {
      // 結帳當下重讀 settings（Codex 第二十一輪）：他端可能剛改 einvoice_enabled，
      // 畫面上的快取值不足採信。以**直接 GET**重讀（非 query.refetch——TanStack v5 的
      // refetch 失敗仍回舊 data，會繞過 fail-closed）：失敗 → 不送單；剛從停用變啟用 →
      // 擋下請店員確認發票欄位（順手更新快取讓欄位顯示），避免以 invoice:null 開出
      // 預設 B2C、不可逆丟失統編/載具/捐贈選擇。
      let freshRes;
      try {
        freshRes = await api.GET("/api/v1/settings");
      } catch {
        // 網路中斷：api.GET 直接 throw（非回 {error}）——包成明確訊息，不讓
        // 「Failed to fetch」外洩給店員。
        throw new Error("無法讀取發票設定，結帳未送出——請重試");
      }
      if (!freshRes.data) {
        throw new Error("無法讀取發票設定，結帳未送出——請重試");
      }
      queryClient.setQueryData(["settings"], freshRes.data); // 讓畫面欄位隨新值顯示
      const freshEnabled = freshRes.data.einvoice_enabled;
      // 任一方向切換都擋（Codex 第二十三輪）：停用→啟用會漏收統編/載具；**啟用→停用**
      // 會把畫面已填的發票欄位靜默丟棄、開出未開發票的單。都先擋下請店員按新狀態重確認
      // （setQueryData 已讓發票欄位隨新值顯示/隱藏）。
      if (freshEnabled !== einvoiceEnabled) {
        throw new Error(
          freshEnabled
            ? "電子發票設定剛變更為啟用：請確認發票欄位（統編/載具/捐贈）後再結帳"
            : "電子發票設定剛變更為停用：本單將不開發票，請確認後再結帳",
        );
      }
      let checkoutCart: DisplayCart | null = null;
      // 正常由 PosCustomerDisplay 註冊櫃檯；若結帳點擊早於該查詢完成，這裡再做一次
      // idempotent 註冊，避免明明已配對卻漏帶權威 cart。註冊失敗時一般付款仍可備援，
      // 購物金則在下方 fail-closed。
      let terminal = displayTerminal;
      if (!terminal) {
        try {
          const registered = await api.POST("/api/v1/customer-display/terminals", {
            body: {
              installation_id: terminalInstallationId(),
              name: "主要櫃檯",
            },
          });
          terminal = registered.data ?? null;
          if (terminal) setDisplayTerminal(terminal);
        } catch {
          terminal = null;
        }
      }
      if (terminal?.paired_kiosk) {
        const current = await api.GET(
          "/api/v1/customer-display/terminals/{terminal_id}/cart/current",
          { params: { path: { terminal_id: terminal.id } } },
        );
        if (!current.data) {
          throw new Error("顧客螢幕購物車尚未同步完成，請稍候後再結帳");
        }
        checkoutCart = current.data;
        setDisplayCart(current.data);
        if (current.data.status === "PAYMENT_UNCERTAIN") {
          throw new Error(
            "這筆付款有沒有成功還不確定，先不要再收一次款。請店長到「付款對帳」查明後再處理。",
          );
        }
        if (plan.storeCredit > 0) {
          if (
            current.data.status !== "FROZEN" ||
            current.data.id !== displayCart?.id ||
            signTaskId == null ||
            !signed
          ) {
            throw new Error("購物金簽署與目前權威購物車不一致，請撤回後重新送簽");
          }
        } else if (current.data.status !== "DRAFT") {
          throw new Error("顧客螢幕購物車目前不可結帳，請重新載入或完成既有流程");
        }
      } else if (plan.storeCredit > 0) {
        throw new Error("購物金付款必須先配對並連線顧客螢幕");
      }
      if (terminal?.paired_kiosk && checkoutCart) {
        const begun = await api.POST(
          "/api/v1/customer-display/terminals/{terminal_id}/cart/begin-checkout",
          {
            params: { path: { terminal_id: terminal.id } },
            body: {
              expected_revision: checkoutCart.revision,
              signature_task_id: plan.storeCredit > 0 ? signTaskId : null,
            },
          },
        );
        if (!begun.data) {
          throw new Error(extractDetail(begun.error) ?? "無法開始結帳，請重新讀取購物車");
        }
        checkoutCart = begun.data;
        setDisplayCart(begun.data);
      }
      const body = {
        lines: toSaleLines(lines),
        buyer_contact_id: member?.id ?? null,
        // 折扣入冪等指紋（body 全量納入簽章）：兩張金額不同的單不得被當成同一張重放。
        adjustments: adjustments.length > 0 ? adjustments : null,
        tenders: toTenders(plan, { linePayKey }) ?? null,
        // 已簽且折抵額相符才綁定（後端亦精確比對＋單次使用守護）。
        signature_task_id: signed && !signMismatch ? signTaskId : null,
        cart_session_id: checkoutCart?.id ?? null,
        cart_revision: checkoutCart?.revision ?? null,
        // 發票資訊（docs/24）：任一欄有值才帶；後端驗互斥與格式並入冪等指紋。
        // 以**結帳當下重讀**的設定判斷（非畫面快取）。
        invoice:
          freshEnabled && (invTaxId !== "" || invCarrier !== "" || invNpoban !== "")
            ? {
                buyer_tax_id: invTaxId !== "" ? invTaxId : null,
                buyer_name: invTaxId !== "" && invBuyerName !== "" ? invBuyerName : null,
                mobile_carrier: invCarrier !== "" ? invCarrier : null,
                npoban: invNpoban !== "" ? invNpoban : null,
              }
            : null,
        // 後端 TOCTOU 防護（Codex 第二十二輪）：帶結帳當下觀察到的設定，後端於交易內
        // 與現值比對，不符 → 409（前端重讀與 POST 間仍有他端切換的殘餘空窗）。
        expected_einvoice_enabled: freshEnabled,
        // 餐飲內用/外帶與桌號（docs/35）：沒有餐飲行時完全不帶（多帶欄位後端會 422）。
        ...dineInRequestFields(hasMenuLine, dineIn),
      };
      // 列印快照於 **await 之前**、與送出 body 同一時點擷取（Codex K6 第二輪）：結帳在途時
      // 店員改動購物車/收款不會污染已提交那筆的簽署證據值（值即後端行鎖驗證的簽署快照）。
      const printSig: CompletedSignature | null =
        body.signature_task_id != null && signTaskId != null
          ? {
              taskId: signTaskId,
              deducted: String(plan.storeCredit),
              remaining: signedBalanceAfter ?? "",
            }
          : null;
      // 冪等鍵綁定送出內容（Codex F3 P2）：同 payload 的網路重試沿用同鍵（後端冪等回原單）；
      // 改了購物車/會員/收款再送則換新鍵，不會被「同鍵不同內容」的 409 卡死。
      // **LINE Pay 例外（docs/30 P3）**：一次性付款碼**不納入**冪等簽章——重掃換碼但購物車不變時，
      // 冪等鍵須保持穩定，後端 orderId（由冪等鍵導出）才能 check-first 防重複扣款（回應遺失後
      // 重掃不會產生新 orderId 而重扣）。故簽章時抹去各 tender 的 line_pay_one_time_key。
      // 指紋對「行/收款順序」不敏感（Codex 第三輪 #1）：重掃同一籃商品但掃描順序不同，須得同
      // 指紋→同鍵→同 orderId，check-first 才能復原、不因換序而重複扣款。故 lines/tenders 各自
      // 正規化排序後才序列化。
      const canonLines = (body.lines ?? [])
        .map((l) => JSON.stringify(l))
        .sort();
      const canonTenders = (body.tenders ?? [])
        .map((t) => JSON.stringify({ ...t, line_pay_one_time_key: null }))
        .sort();
      const sigBody = {
        ...body,
        // revision 是伺服器併發控制版本；PAYMENT_UNCERTAIN→對帳會遞增，但交易內容與
        // LINE Pay orderId 不得因此換鍵，否則可能重複扣款。購物車 id 仍保留在指紋中。
        cart_revision: null,
        lines: canonLines,
        tenders: canonTenders,
        // 折扣目標改用購物車列的**穩定 key**：lines 已排序，目標若留位置索引，
        // 換序重掃就會換出新的冪等鍵與 LINE Pay orderId（已扣款卻找不回原單 → 可能重扣）。
        adjustments: canonicalAdjustments(activeDiscounts, lines),
      };
      const sig = JSON.stringify(sigBody);
      // 冪等鍵**持久化**（Codex 第二輪 #2）：以購物車指紋（不含一次性付款碼）為界存 localStorage，
      // 跨頁面重整/重掛存活——LINE Pay 若已扣款但本地 commit 前崩潰/回應遺失，重整後重掃同購物車
      // 沿用同鍵 → 同 orderId → 後端 check-first 復原、不重複扣款。成功後清（見 onSuccess）。
      const idemKey = getOrCreatePersistedIdemKey("pos-checkout", sig);
      const { data, error } = await api.POST("/api/v1/sales", {
        params: { header: { "Idempotency-Key": idemKey } },
        body,
      });
      if (!data) throw new Error(extractDetail(error) ?? "結帳失敗");
      return { sale: data, sig: printSig };
    },
    onSuccess: ({ sale, sig }) => {
      // 結帳成立 → 清除持久化冪等鍵（Codex 第二輪 #2），下一筆換新鍵。
      clearPersistedIdemKey("pos-checkout");
      setCompleted(sale);
      setCompletedCampaign(campaignNote);
      // 簽署證據快照＝mutationFn 於送出當下擷取的不可變值（非 callback 時的活狀態）。
      setCompletedSignature(sig);
      setShowDialog(true);
      // 電子發票：結帳成立後自動開立＋（可印時）送印證明聯；失敗只提示、不影響交易。
      // 以**後端回傳的 invoice_status** 為權威（Codex 第十八輪）：settings 查詢延遲/失敗
      // 時前端旗標可能為 false，但後端已建 PENDING 發票——不得因此漏開立。
      setCompletedInvoice(null);
      setInvoiceNote(null);
      if (sale.invoice_status === "PENDING_ISSUE") {
        setInvoiceNote("發票開立中…");
        issueInvoice.mutate(sale);
      }
      // 收現才開錢櫃（docs/10 §5）；純購物金不碰現金、不開櫃。
      // fire-and-forget：交易已寫後端，開櫃失敗只在完成畫面提示、不擋流程。
      if (plan.cash > 0) {
        setDrawerNotice(null);
        openCashDrawer().catch((err: Error) => setDrawerNotice(err.message));
      }
      startKitchenTicket(sale, printKitchenEnabled);
    },
    onError: (err: Error) => {
      setNotice(err.message);
      if (displayTerminal) {
        void api
          .GET("/api/v1/customer-display/terminals/{terminal_id}/cart/current", {
            params: { path: { terminal_id: displayTerminal.id } },
          })
          .then(async ({ data }) => {
            setDisplayCart(data ?? null);
            if (data?.status === "COMPLETED" && data.sale_id != null) {
              await showCompletedCart(
                data,
                "交易已在後端完成；已從顧客螢幕工作階段恢復完成畫面。",
              );
            }
          });
      }
      // LINE Pay 結帳失敗：一次性付款碼已作廢（單次使用/已過期）→ 清空，提示店員重新掃碼。
      // 冪等鍵已排除付款碼、保持穩定，重掃不會產生新 orderId 重複扣款（見上 sigBody）。
      if (plan.linePay > 0 && !err.message.includes("PAYMENT_UNCERTAIN")) {
        setLinePayKey("");
      }
    },
  });

  function addToCart(line: CartLine) {
    if (cartMutationLocked) {
      setNotice("簽署或付款處理期間，購物車已由伺服器鎖定。");
      return;
    }
    const result = addLine(lines, line);
    setLines(result.lines);
    setNotice(
      result.duplicateSerialized
        ? `${line.description} 已在購物車（序號品不可重複）`
        : null,
    );
  }

  /**
   * 所有結帳入口的唯一出口。
   *
   * 主結帳鍵之外還有備註提醒對話框的「已確認，繼續結帳」——那顆先前直接呼叫
   * checkout.mutate，繞過了還原鎖：對話框開著期間若終端重抓或事後配對讓還原重新
   * 待決，仍會用即將被取代的購物車開始結帳。集中在這裡，只有一處要維護。
   */
  function startCheckout() {
    // 購物車尚未定案（還在確認有沒有未結完的單／正在還原）→ 一律不送出。
    if (restoring || restorePending) return;
    checkout.mutate();
  }

  function resetSale() {
    setNoteAck("");
    setNoteDialogOpen(false);
    // 完成畫面是 early return，PosCustomerDisplay 在那時已卸載；回到購物車視圖會重新
    // 掛載並重新問一次「有沒有未結完的購物車」。子元件的 effect 要下一個 commit 才會
    // 回報，這中間父層若還留著 false 就有一個 render 的破口——先 fail closed。
    setRestorePending(true);
    setLines([]);
    setDiscountDrafts([]);
    setGiftTargetKey(null);
    setDiscountTargetKey(null);
    setMember(null);
    setMode("CASH");
    setStoreCreditInput("");
    setMixedRemainder("CASH");
    setTaiwanPayConfirmed(false);
    setReceivedInput("");
    setLinePayKey(""); // 一次性付款碼用畢清空、不重用（下一單重新掃）
    setNotice(null);
    setCompleted(null);
    setCompletedCampaign(null);
    setShowDialog(false);
    setDrawerNotice(null);
    setSignTaskId(null); // 本單完成/重來，下一單重新推送簽署
    setDisplayCart(null);
    setReconcileReason("");
    setReconcileEvidenceType("");
    setReconcileEvidenceReference("");
    setCompletedSignature(null);
    setInvTaxId("");
    setInvBuyerName("");
    setInvCarrier("");
    setInvNpoban("");
    setInvoiceNote(null);
    setCompletedInvoice(null);
    setDineIn(clearDineIn());
    // 連同 ref 一起失效：在途列印之後回來要走「遲到警告」，不能被當成當前這張完成頁。
    applyKitchen(null);
    reprintKitchen.reset();
    issueInvoice.reset();
    printProof.reset();
    // 開新一筆：清除任何殘留的持久化結帳冪等鍵（Codex 第二輪 #2）。
    clearPersistedIdemKey("pos-checkout");
    checkout.reset();
  }

  // 完成畫面（結帳成功後）
  if (completed !== null) {
    return (
      <section>
        <h1 className="page-title">POS 結帳</h1>
        <div className="card pos-complete">
          <h2>
            {completed.payment_method === "LINE_PAY" ? "LINE Pay 收款成功" : "已完成"}{" "}
            <span className="badge-open">#{completed.id}</span>
          </h2>
          <dl className="stat-list">
            <div className="stat">
              <dt>總額</dt>
              <dd>
                <Money value={parseNtd(completed.total) ?? 0} />
              </dd>
            </div>
            <div className="stat">
              <dt>收款方式</dt>
              <dd>{formatSalePaymentSummary(completed)}</dd>
            </div>
          </dl>
          {drawerNotice !== null && (
            <p role="alert" className="form-error">
              錢櫃未開啟：{drawerNotice}（交易已完成，請以鑰匙開櫃）
            </p>
          )}
          {staleKitchen.map((item) => (
            <p key={item.saleId} role="alert" className="form-error pos-kitchen-stale">
              {item.message}
              <button
                type="button"
                className="btn-ghost"
                onClick={() =>
                  setStaleKitchen((prev) => prev.filter((x) => x.saleId !== item.saleId))
                }
              >
                知道了
              </button>
            </p>
          ))}
          {kitchen !== null && (
            <p
              className={kitchen.outcome === "FAILED" ? "form-error" : "hint"}
              role={kitchen.outcome === "FAILED" ? "alert" : undefined}
            >
              {kitchen.outcome === "FAILED"
                ? `出餐單列印失敗：${kitchen.error}（吧台不會收到單，請重印或口頭通知）`
                : kitchen.outcome === "SKIPPED"
                  ? `${describeKitchenTarget(kitchen)}：本店已關閉自動列印出餐單，需要時可手動列印。`
                  : kitchen.outcome === "PENDING"
                    ? `出餐單列印中…（${describeKitchenTarget(kitchen)}）`
                    : `出餐單已送出列印（${describeKitchenTarget(kitchen)}）。`}
              <button
                type="button"
                className="btn-ghost pos-kitchen-reprint"
                // 自動列印還在跑時不可再送：兩張單都會印出來，吧台可能做兩份。
                disabled={reprintKitchen.isPending || kitchen.outcome === "PENDING"}
                onClick={() => reprintKitchen.mutate({ sale: completed })}
              >
                {reprintKitchen.isPending
                  ? "列印中…"
                  : kitchen.outcome === "SKIPPED"
                    ? "列印出餐單"
                    : "重印出餐單"}
              </button>
            </p>
          )}
          {invoiceNote !== null && (
            <p className="hint pos-invoice-note">
              {completedInvoice?.invoice_no != null
                ? `發票 ${completedInvoice.invoice_no}：`
                : ""}
              {invoiceNote}
              {issueInvoice.isError && (
                <button
                  type="button"
                  className="btn-ghost pos-invoice-retry"
                  disabled={issueInvoice.isPending}
                  onClick={() => issueInvoice.mutate(completed)}
                >
                  重試開立
                </button>
              )}
              {completedInvoice != null && invoiceProofPrintable(completedInvoice) && (
                // 常駐重印（Codex 第十六輪）：抬頭慢載入/代理離線/缺紙時列印可能失敗，
                // 發票已開立不會進 error 態——店員需有在地重試入口。
                <button
                  type="button"
                  className="btn-ghost pos-invoice-reprint"
                  disabled={printProof.isPending}
                  onClick={() =>
                    printProof.mutate({ invoice: completedInvoice, sale: completed })
                  }
                >
                  {printProof.isPending ? "列印中…" : "重印證明聯"}
                </button>
              )}
            </p>
          )}
          <div className="pos-dialog-actions">
            <button
              type="button"
              className="btn-ghost"
              onClick={() => setShowDialog(true)}
            >
              列印商品明細
            </button>
            <button type="button" className="btn-primary" onClick={resetSale}>
              開始下一筆
            </button>
          </div>
        </div>
        {showDialog && (
          <PrintDialog
            sale={completed}
            campaignName={completedCampaign}
            signature={completedSignature}
            onClose={() => setShowDialog(false)}
          />
        )}
      </section>
    );
  }


  return (
    <section>
      <h1 className="page-title">POS 結帳</h1>
      <PosCustomerDisplay
        lines={saleLines}
        adjustments={adjustments}
        buyerContactId={member?.id ?? null}
        tenders={customerDisplayTenders}
        ready={quoteReady}
        serviceMode={dineIn.mode}
        tableNo={dineIn.tableNo}
        onRestore={restoreCustomerDisplayCart}
        onTerminalChange={setDisplayTerminal}
        onCartChange={setDisplayCart}
        onSyncDirtyChange={setCartSyncDirty}
        onRestorePendingChange={setRestorePending}
      />
      {staleKitchen.map((item) => (
        <p key={item.saleId} role="alert" className="form-error pos-kitchen-stale">
          {item.message}
          <button
            type="button"
            className="btn-ghost"
            onClick={() =>
              setStaleKitchen((prev) => prev.filter((x) => x.saleId !== item.saleId))
            }
          >
            知道了
          </button>
        </p>
      ))}
      <ActiveCampaignBanner />
      <div className="pos-grid">
        <div className="pos-left">
          <ScanBar
            onResolved={addToCart}
            disabled={cartMutationLocked}
            disabledReason={
              restoring
                ? "正在恢復上一筆購物車，稍候再掃。"
                : restorePending
                  ? "正在確認有沒有未結完的購物車，稍候再掃。"
                  : undefined
            }
          />
          {notice !== null && (
            <p role="alert" className="form-error">
              {notice}
            </p>
          )}
          {lines.length === 0 ? (
            <p className="pos-empty hint">
              掃描或輸入商品條碼，或點下方餐飲菜單開始結帳。
            </p>
          ) : (
            <div
              className="pos-cart-scroll"
              role="region"
              aria-label="購物車明細"
              tabIndex={0}
            >
              <table className="pos-cart">
                <thead>
                  <tr>
                    <th>品項</th>
                    <th>單價</th>
                    <th>數量</th>
                    <th>小計</th>
                    <th aria-label="操作" />
                  </tr>
                </thead>
                <tbody>
                  {lines.map((line, i) => {
                    // 逐行折後：試算就緒時用 quote 同序行的折後單價/小計；有折扣則加顯原價刪除線。
                    const ql = quotedLines?.[i];
                    const giftLine = ql ? ql.line_kind === "GIFT" : isGift(line);
                    const discounted =
                      ql != null &&
                      ql.discount_amount !== "0" &&
                      ql.original_unit_price != null;
                    const unitVal = ql
                      ? (parseNtd(ql.unit_price) ?? line.unitPrice)
                      : line.unitPrice;
                    // 小計認**實付**（net_amount）：臨時折扣落在這裡，line_total 仍是活動折後牌價。
                    const subtotalVal = ql
                      ? (parseNtd(ql.net_amount) ?? lineTotal(line))
                      : lineTotal(line);
                    // 贈品也顯示原價刪除線——讓客人與店員都看得到「送了多少價值」。
                    const originalUnit =
                      (discounted || giftLine) && ql?.original_unit_price != null
                        ? (parseNtd(ql.original_unit_price) ?? line.unitPrice)
                        : null;
                    return (
                      <tr key={line.key}>
                        <td>
                          {line.description}
                          {isGift(line) && (
                            <span className="pos-gift-badge">贈品</span>
                          )}
                          {line.note != null && line.note.trim() !== "" && (
                            <span className="pos-line-note">備註：{line.note.trim()}</span>
                          )}
                        </td>
                        <td>
                          {originalUnit !== null ? (
                            <span className="pos-price-discounted">
                              <s className="pos-price-original">
                                <Money value={originalUnit} />
                              </s>{" "}
                              <Money value={unitVal} />
                            </span>
                          ) : (
                            <Money value={unitVal} />
                          )}
                        </td>
                        <td>
                          {line.lineType === "SERIALIZED" ? (
                            1
                          ) : (
                            <input
                              className="pos-qty"
                              inputMode="numeric"
                              value={line.qty}
                              aria-label={`${line.description} 數量`}
                              disabled={cartMutationLocked}
                              onChange={(e) =>
                                setLines(
                                  setQty(
                                    lines,
                                    line.key,
                                    parseNtd(e.target.value) ?? 1,
                                  ),
                                )
                              }
                            />
                          )}
                        </td>
                        <td>
                          <Money value={subtotalVal} />
                        </td>
                        <td className="pos-line-actions">
                          {isGift(line) ? (
                            <button
                              type="button"
                              className="btn-ghost"
                              aria-label={`取消贈品 ${line.description}`}
                              disabled={cartMutationLocked}
                              onClick={() =>
                                setLines(unmarkGift(lines, line.key))
                              }
                            >
                              取消贈品
                            </button>
                          ) : (
                            <>
                              <button
                                type="button"
                                className="btn-ghost"
                                aria-label={`折扣 ${line.description}`}
                                disabled={cartMutationLocked}
                                onClick={() => {
                                  setDiscountScopeIsOrder(false);
                                  setDiscountTargetKey(line.key);
                                }}
                              >
                                折扣
                              </button>
                              <button
                                type="button"
                                className="btn-ghost"
                                aria-label={`改為贈品 ${line.description}`}
                                disabled={cartMutationLocked}
                                onClick={() => setGiftTargetKey(line.key)}
                              >
                                改為贈品
                              </button>
                            </>
                          )}
                          <button
                            type="button"
                            className="btn-ghost"
                            aria-label={`移除 ${line.description}`}
                            disabled={cartMutationLocked}
                            onClick={() => {
                              const next = removeLine(lines, line.key);
                              setLines(next);
                              // 移除最後一筆餐飲 → 清空內用/外帶，否則純二手的單會
                              // 帶著桌號送出（後端 422）。在這裡清而不是用 effect：
                              // 這是唯一會讓餐飲行變少的路徑，也避免 setState-in-effect。
                              if (!next.some((l) => l.lineType === "MENU")) {
                                setDineIn(clearDineIn());
                              }
                            }}
                          >
                            移除
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <MenuPanel onAdd={addToCart} disabled={cartMutationLocked} />
        </div>
        {giftTargetKey !== null && (
          <GiftDialog
            lineDescription={
              lines.find((l) => l.key === giftTargetKey)?.description ?? ""
            }
            onCancel={() => setGiftTargetKey(null)}
            onConfirm={(reasonId, note) => {
              setLines(
                markAsGift(lines, giftTargetKey, {
                  reasonId,
                  note: note === "" ? undefined : note,
                }),
              );
              // 改成贈品的那一列 key 會換前綴，指向它的單品折扣一併失效（贈品不參與折扣）。
              setDiscountDrafts((prev) =>
                prev.filter((d) => d.targetKey !== giftTargetKey),
              );
              setGiftTargetKey(null);
            }}
          />
        )}
        {(discountTargetKey !== null || discountScopeIsOrder) && (
          <DiscountDialog
            scopeLabel={
              discountScopeIsOrder
                ? "整單折扣"
                : `折扣：${lines.find((l) => l.key === discountTargetKey)?.description ?? ""}`
            }
            onCancel={() => {
              setDiscountTargetKey(null);
              setDiscountScopeIsOrder(false);
            }}
            onConfirm={(draft) => {
              setDiscountDrafts((prev) => [
                ...prev,
                {
                  id: `${Date.now()}-${prev.length}`,
                  scope: discountScopeIsOrder ? "ORDER" : "ITEM",
                  targetKey: discountScopeIsOrder ? null : discountTargetKey,
                  method: draft.method,
                  value: draft.value,
                  reasonId: draft.reasonId,
                  note: draft.note,
                },
              ]);
              setDiscountTargetKey(null);
              setDiscountScopeIsOrder(false);
            }}
          />
        )}

        <aside className="pos-right card">
          {/* 金額摘要：贈品價值單獨列出，**不加進應付再折掉**——它不是折扣，
              報表也各走各的欄位。 */}
          {lines.length > 0 && quoteReady && quote.data && (
            <dl className="pos-summary">
              {itemDiscountTotal > 0 && (
                <div>
                  <dt>商品折扣</dt>
                  <dd>
                    −<Money value={itemDiscountTotal} />
                  </dd>
                </div>
              )}
              {orderDiscountTotal > 0 && (
                <div>
                  <dt>整單折扣</dt>
                  <dd>
                    −<Money value={orderDiscountTotal} />
                  </dd>
                </div>
              )}
              {giftRetailValue > 0 && (
                <div>
                  <dt>贈品價值</dt>
                  <dd>
                    <Money value={giftRetailValue} />
                    <span className="hint">（僅供參考，不計入應付）</span>
                  </dd>
                </div>
              )}
            </dl>
          )}
          <div className="pos-total">
            <span>應付總額</span>
            <strong>
              <Money value={total} />
            </strong>
          </div>
          {campaignNote && (
            <p className="hint pos-campaign-note">已套用活動折扣：{campaignNote}</p>
          )}
          <div className="pos-discount-panel">
            <button
              type="button"
              className="btn-ghost"
              disabled={cartMutationLocked || lines.length === 0}
              onClick={() => {
                setDiscountScopeIsOrder(true);
                setDiscountTargetKey(null);
              }}
            >
              整單折扣
            </button>
            {activeDiscounts.length > 0 && (
              <ul className="pos-discount-list">
                {activeDiscounts.map((draft) => (
                  <li key={draft.id}>
                    <span>{describeDiscount(draft, lines)}</span>
                    <button
                      type="button"
                      className="btn-ghost"
                      aria-label={`移除折扣 ${describeDiscount(draft, lines)}`}
                      disabled={cartMutationLocked}
                      onClick={() =>
                        setDiscountDrafts((prev) =>
                          prev.filter((d) => d.id !== draft.id),
                        )
                      }
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {lines.length > 0 && quote.isError && (
            <p role="alert" className="form-error">
              試算失敗：{(quote.error as Error).message}
            </p>
          )}

          {/* 餐飲內用/外帶（docs/35）：只在購物車有餐飲行時出現；桌號按鈕來自設定清單。 */}
          {hasMenuLine && (
            <div className="pos-dinein-panel">
              <h3>內用 / 外帶</h3>
              <div
                className="pos-dinein-modes"
                role="radiogroup"
                aria-label="內用或外帶"
              >
                <button
                  type="button"
                  role="radio"
                  aria-checked={dineIn.mode === "DINE_IN"}
                  className={`pos-dinein-mode ${dineIn.mode === "DINE_IN" ? "is-active" : ""}`}
                  disabled={cartMutationLocked || dineInValidation.tablesUnavailable}
                  onClick={() => setDineIn({ mode: "DINE_IN", tableNo: null })}
                >
                  內用
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={dineIn.mode === "TAKEOUT"}
                  className={`pos-dinein-mode ${dineIn.mode === "TAKEOUT" ? "is-active" : ""}`}
                  disabled={cartMutationLocked}
                  onClick={() => setDineIn({ mode: "TAKEOUT", tableNo: null })}
                >
                  外帶
                </button>
              </div>
              {dineIn.mode === "DINE_IN" && dineInTables.length > 0 && (
                <div className="pos-dinein-tables" role="radiogroup" aria-label="桌號">
                  {dineInTables.map((table) => (
                    <button
                      key={table}
                      type="button"
                      role="radio"
                      aria-checked={dineIn.tableNo === table}
                      className={`pos-dinein-table ${dineIn.tableNo === table ? "is-active" : ""}`}
                      disabled={cartMutationLocked}
                      onClick={() => setDineIn({ mode: "DINE_IN", tableNo: table })}
                    >
                      {table}
                    </button>
                  ))}
                </div>
              )}
              {dineInValidation.error !== null && (
                <p className="hint pos-dinein-error">{dineInValidation.error}</p>
              )}
            </div>
          )}

          <MemberPanel
            member={member}
            onSelect={setMember}
            onClear={() => setMember(null)}
            disabled={cartMutationLocked}
          />

          <TenderPanel
            cartHasItems={lines.length > 0}
            total={total}
            hasMember={member !== null}
            memberBalance={memberBalance}
            drawerOpen={drawerOpen}
            storeCreditMax={storeCreditMax}
            storeCreditMinSpend={storeCreditMinSpend}
            taiwanpayFeePct={settings.data?.taiwanpay_fee_pct ?? "0"}
            linepayEnabled={settings.data?.linepay_enabled === true}
            linepayFeePct={settings.data?.linepay_fee_pct ?? "0"}
            linePayKey={linePayKey}
            setLinePayKey={setLinePayKey}
            mode={mode}
            setMode={setMode}
            storeCreditInput={storeCreditInput}
            setStoreCreditInput={setStoreCreditInput}
            mixedRemainder={mixedRemainder}
            setMixedRemainder={setMixedRemainder}
            taiwanPayConfirmed={taiwanPayConfirmed}
            setTaiwanPayConfirmed={setTaiwanPayConfirmed}
            receivedInput={receivedInput}
            setReceivedInput={setReceivedInput}
            disabled={cartMutationLocked}
          />

          {/* 購物金扣抵手持簽署（docs/23 K5，D3）：客人於手持端核對折抵/剩餘後手寫簽名 */}
          {plan.storeCredit > 0 && member !== null && (
            <div className="pos-sign-panel">
              <h3>扣抵確認簽署</h3>
              {signTaskId == null ? (
                <>
                  <p className="hint">
                    購物金扣抵須由客人在已配對的顧客螢幕核對內容並簽名。
                  </p>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={
                      pushSign.isPending ||
                      !quoteReady ||
                      displayCart?.status !== "DRAFT" ||
                      displayTerminal?.paired_kiosk?.online !== true ||
                      // 含餐飲卻沒選內用/外帶就送簽 → 客人簽完名後結帳才被擋，而那時
                      // 內用/外帶鍵已因購物車凍結而停用，只能撤回重簽（QA BUG-004）。
                      // 錯誤訊息本來就顯示在上方的餐飲區塊，店員看得到原因。
                      !dineInValidation.ok ||
                      // 而且畫面上的購物車要**已經同步到伺服器**：同步有 180ms 防抖，
                      // 改完立刻送簽的話凍結到的是上一版，客人簽的內容與實際結帳對不
                      // 起來，一樣得撤回重簽。用整車指紋而不是只比餐飲欄位——移除最後
                      // 一筆餐飲時，餐飲比對會立刻「一致」，伺服器上卻還是舊車
                      //（Codex 第三輪）。
                      cartSyncDirty
                    }
                    onClick={() => pushSign.mutate()}
                  >
                    送至手持裝置簽署
                  </button>
                </>
              ) : signTaskFinishedWithoutSale ? (
                <>
                  <p role="alert" className="form-error">
                    {signTask.data?.status === "EXPIRED"
                      ? "客人太久沒有簽名，購物車已解鎖；請重新送出給客人簽。"
                      : signTask.data?.status === "FAILED"
                        ? "這次結帳沒有成功。剛才那次簽名已存檔備查，重新結帳要請客人再簽一次。"
                        : "簽署已作廢，請重新送出。"}
                  </p>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setSignTaskId(null)}
                  >
                    建立新簽署
                  </button>
                </>
              ) : !signed ? (
                <>
                  <p className="hint">
                    {signTask.data?.status === "SIGNING"
                      ? "客人正在核對並簽署；購物車已由伺服器鎖定。"
                      : "已送至顧客螢幕，等待客人開啟簽署畫面…"}
                  </p>
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={cancelSign.isPending}
                    onClick={() => cancelSign.mutate(false)}
                  >
                    撤回簽署並修改
                  </button>
                </>
              ) : signMismatch ? (
                <>
                  <p role="alert" className="form-error">
                    交易內容已變更（客人簽的是折抵 ${signedDebit}／合計 ${signedTotal}），與目前
                    結帳不符：請作廢重推簽署。
                  </p>
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={cancelSign.isPending}
                    onClick={() => cancelSign.mutate(false)}
                  >
                    撤回簽署並修改
                  </button>
                </>
              ) : (
                <>
                  <p className="pos-sign-done">
                    ✓ 客人已完成簽署（折抵 <Money value={plan.storeCredit} />）
                  </p>
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={cancelSign.isPending}
                    onClick={() => cancelSign.mutate(false)}
                  >
                    撤回簽署並修改
                  </button>
                </>
              )}
              {signTaskId !== null &&
                !signTaskFinishedWithoutSale &&
                displayCart?.status !== "PAYMENT_UNCERTAIN" &&
                displayTerminal?.paired_kiosk?.online === false && (
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={cancelSign.isPending}
                    onClick={() => cancelSign.mutate(true)}
                  >
                    顧客螢幕故障，撤回並改用現金
                  </button>
                )}
            </div>
          )}

          {displayCart?.status === "PAYMENT_UNCERTAIN" && (
            <section className="pos-sign-panel" aria-labelledby="payment-reconcile-title">
              <h3 id="payment-reconcile-title">LINE Pay 付款待對帳</h3>
              <p role="alert" className="form-error">
                付款結果不明期間，本筆購物車已鎖定；請勿重新刷付款碼或建立新交易。
              </p>
              {"payment_order_id" in displayCart && displayCart.payment_order_id && (
                <p className="hint">交易識別：{displayCart.payment_order_id}</p>
              )}
              {isManager ? (
                <>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={reconcilePayment.isPending}
                    onClick={() => reconcilePayment.mutate("QUERY_PROVIDER")}
                  >
                    {reconcilePayment.isPending ? "查詢中…" : "向 LINE Pay 查詢結果"}
                  </button>
                  <details>
                    <summary>LINE Pay 那邊還是查不到時，由店長依實際情況判定</summary>
                    <label className="field">
                      <span className="field-label">判定的依據</span>
                      <input
                        value={reconcileReason}
                        onChange={(event) => setReconcileReason(event.target.value)}
                        maxLength={300}
                      />
                    </label>
                    <label className="field">
                      <span className="field-label">證據類型</span>
                      <input
                        value={reconcileEvidenceType}
                        onChange={(event) =>
                          setReconcileEvidenceType(event.target.value)
                        }
                        placeholder="例如：LINE Pay 對帳單截圖"
                        maxLength={60}
                      />
                    </label>
                    <label className="field">
                      <span className="field-label">外部交易識別／證據位置</span>
                      <input
                        value={reconcileEvidenceReference}
                        onChange={(event) =>
                          setReconcileEvidenceReference(event.target.value)
                        }
                        maxLength={200}
                      />
                    </label>
                    <div className="pos-dialog-actions">
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={reconcilePayment.isPending}
                        onClick={() => reconcilePayment.mutate("MANUAL_SUCCESS")}
                      >
                        裁定付款成功
                      </button>
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={reconcilePayment.isPending}
                        onClick={() => reconcilePayment.mutate("MANUAL_FAILED")}
                      >
                        裁定付款失敗
                      </button>
                    </div>
                  </details>
                </>
              ) : (
                <p className="hint">請店長登入這一頁，才能向 LINE Pay 查詢或自行判定結果。</p>
              )}
            </section>
          )}

          {/* 發票區（docs/10 §5/§6）：讀不到設定時不可逕自當「不開票」（Codex F3 P3）。 */}
          {settings.isError ? (
            <p role="alert" className="form-error pos-invoice-off">
              無法讀取發票設定，請重試。
            </p>
          ) : settings.isPending ? (
            <p className="hint pos-invoice-off">讀取發票設定中…</p>
          ) : einvoiceEnabled ? (
            // 發票資訊（docs/24）：統編（=B2B）/手機載具/捐贈碼三擇一（互斥；後端亦驗）。
            // 全空＝B2C 一般開立、結帳後自動印證明聯。
            <fieldset className="pos-invoice">
              <legend className="field-label">電子發票（三擇一，全空＝一般開立並列印）</legend>
              <label className="field">
                <span className="field-label">買方統編（B2B）</span>
                <input
                  name="inv-tax-id"
                  inputMode="numeric"
                  placeholder="8 碼數字"
                  value={invTaxId}
                  disabled={invCarrier !== "" || invNpoban !== ""}
                  onChange={(e) => setInvTaxId(e.target.value.trim())}
                />
                {invTaxIdBad && <span className="form-error">統編須為 8 碼數字</span>}
              </label>
              {invTaxId !== "" && (
                <label className="field">
                  <span className="field-label">買方名稱（選填）</span>
                  <input
                    name="inv-buyer-name"
                    value={invBuyerName}
                    onChange={(e) => setInvBuyerName(e.target.value)}
                  />
                </label>
              )}
              <label className="field">
                <span className="field-label">手機載具（掃描條碼，/ 開頭 8 碼）</span>
                <input
                  name="inv-carrier"
                  placeholder="/XXXXXXX"
                  value={invCarrier}
                  disabled={invTaxId !== "" || invNpoban !== ""}
                  onChange={(e) => setInvCarrier(e.target.value.trim().toUpperCase())}
                />
                {invCarrierBad && (
                  <span className="form-error">載具須為 / 開頭＋7 碼（數字/大寫/+-.）</span>
                )}
              </label>
              <label className="field">
                <span className="field-label">捐贈碼</span>
                <input
                  name="inv-npoban"
                  inputMode="numeric"
                  placeholder="3–7 碼數字"
                  value={invNpoban}
                  disabled={invTaxId !== "" || invCarrier !== ""}
                  onChange={(e) => setInvNpoban(e.target.value.trim())}
                />
                {invNpobanBad && <span className="form-error">捐贈碼須為 3–7 碼數字</span>}
              </label>
            </fieldset>
          ) : (
            <p className="hint pos-invoice-off">
              這筆不開發票（本店尚未啟用電子發票）。
            </p>
          )}

          {checkout.isError && (
            <p role="alert" className="form-error">
              {(checkout.error as Error).message}
            </p>
          )}

          <button
            type="button"
            className="btn-primary pos-checkout"
            disabled={
              !validation.ok ||
              checkout.isPending ||
              // 還原中／還在確認有沒有未結完的購物車：購物車都還沒定案，此時結帳
              // 會用到即將被取代的內容（也可能漏掉備註提醒）。
              restoring ||
              restorePending ||
              !quoteReady ||
              scSignBlock ||
              displayCart?.status === "PAYMENT_UNCERTAIN" ||
              invoiceInputBad ||
              // fail-closed（Codex 第十九/二十輪）：結帳需要**新鮮的** settings——
              // pending/失敗、掛載後重抓仍在途（isFetching）、或上次重抓失敗
              // （failureCount>0，快取值可能過期）時 einvoiceEnabled 都不可信：若後端
              // 實際已啟用，結帳會以 invoice:null 開出預設 B2C、不可逆丟失統編/載具/
              // 捐贈選擇。擋結帳待設定讀取成功。
              !settings.isSuccess ||
              settings.isFetching ||
              settings.failureCount > 0 ||
              // 含餐飲卻沒選內用/外帶或桌號 → 先擋（後端亦有同一組守衛）。
              !dineInValidation.ok
            }
            onClick={() => {
              // 有未確認的商品備註 → 先跳提醒，確認後才進收款（不直接送出）。
              if (noteAckPending) {
                setNoteDialogOpen(true);
                return;
              }
              startCheckout();
            }}
          >
            {/* 兩者可能同時為真（還原尚未完成 → restorePending 也還是 true）。
                已經在還原就說「還原中」，那是比較具體的狀態。 */}
            {restoring
              ? "還原購物車中…"
              : restorePending
                ? "確認購物車中…"
                : checkout.isPending
              ? "結帳中…"
              : displayCart?.status === "PAYMENT_UNCERTAIN"
                ? "等待付款對帳…"
              : lines.length > 0 && !quoteReady
                ? "試算中…"
                : scSignBlock && signTaskId != null
                  ? "等待簽署…"
                  : "結帳"}
          </button>
        </aside>
      </div>
      {noteDialogOpen && (
        <div
          className="pos-dialog-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="商品備註提醒"
        >
          <div className="card pos-dialog pos-note-dialog">
            <h2>交貨前請先確認</h2>
            <p className="hint">
              這筆有 {notedLines.length} 件商品帶備註，請先跟客人說明或處理完再收款。
            </p>
            <ul className="pos-note-list">
              {notedLines.map((line) => (
                <li key={line.key}>
                  <span className="pos-note-item">{line.description}</span>
                  <span className="pos-note-body">{line.note}</span>
                </li>
              ))}
            </ul>
            <div className="pos-dialog-actions">
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  setNoteAck(noteFingerprint);
                  setNoteDialogOpen(false);
                  startCheckout();
                }}
              >
                已確認，繼續結帳
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setNoteDialogOpen(false)}
              >
                回購物車
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
