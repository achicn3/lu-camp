"use client";
// /pos 結帳（docs/10 §5、docs/16 §3.2）：掃碼加入購物車（序號品／散裝堆）→ 會員歸戶（選填）
// → 收款（現金／購物金／混合）→ 結帳 POST /sales →（完成後）詢問是否列印商品明細。
// einvoice_enabled=false 時發票區隱藏（顯示「本期不開票」），載具輸入待啟用後再開。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from "react";

import { discountDisplay } from "@/features/campaigns/campaigns";
import {
  type CartLine,
  addLine,
  cartTotal,
  lineTotal,
  removeLine,
  setQty,
  toSaleLines,
} from "@/features/pos/cart";
import {
  type MixedRemainderMethod,
  type TenderMode,
  changeDue,
  resolvePlan,
  toTenders,
  validatePlan,
} from "@/features/pos/tender";
import { openCashDrawer, printEInvoice, printSaleDetail } from "@/lib/agent";
import { fetchSignaturePngBase64 } from "@/lib/signature";
import { api } from "@/lib/api";
import { decodeSession } from "@/lib/auth";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";
import {
  clearPersistedIdemKey,
  getOrCreatePersistedIdemKey,
} from "@/lib/idempotency";

type SaleRead = components["schemas"]["SaleRead"];
type InvoiceRead = components["schemas"]["InvoiceRead"];
type ContactRead = components["schemas"]["ContactRead"];
type CampaignRead = components["schemas"]["CampaignRead"];
type MenuItemRead = components["schemas"]["MenuItemRead"];

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

function ScanBar({ onResolved }: { onResolved: (line: CartLine) => void }) {
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
        };
      }
      // 僅 404 才視為「非序號品」改試散裝；其他狀態（401/403/500）如實回報，
      // 不可把後端錯誤偽裝成「找不到此條碼」（Codex F3 P3）。
      if (serialized.response.status !== 404) {
        throw new Error(
          extractDetail(serialized.error) ??
            `查詢失敗（HTTP ${serialized.response.status}）`,
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
        };
      }
      if (bulk.response.status !== 404) {
        throw new Error(
          extractDetail(bulk.error) ??
            `查詢失敗（HTTP ${bulk.response.status}）`,
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
        };
      }
      if (catalog.response.status !== 404) {
        throw new Error(
          extractDetail(catalog.error) ??
            `查詢失敗（HTTP ${catalog.response.status}）`,
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
    if (!value || mutation.isPending) return;
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
          disabled={mutation.isPending}
        />
      </label>
      <span className="hint pos-scan-hint">
        {mutation.isPending ? "查詢中…" : "掃描後自動加入購物車（免按 Enter）。"}
      </span>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </form>
  );
}

// ── 會員歸戶 ──
function MemberPanel({
  member,
  onSelect,
  onClear,
}: {
  member: ContactRead | null;
  onSelect: (c: ContactRead) => void;
  onClear: () => void;
}) {
  const [q, setQ] = useState("");
  const search = useMutation({
    mutationFn: async (query: string): Promise<ContactRead[]> => {
      const { data, error } = await api.GET("/api/v1/contacts", {
        params: { query: { q: query, role: "MEMBER", limit: 8 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "查詢失敗");
      return data;
    },
  });
  const balance = useQuery({
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

  if (member !== null) {
    const bal = balance.data ? (parseNtd(balance.data.balance) ?? 0) : null;
    return (
      <div className="pos-member pos-member-selected">
        <div>
          <strong>{member.name}</strong>
          {member.phone && <span className="hint"> · {member.phone}</span>}
          <div className="hint">
            點數 {member.member_points} · 購物金餘額{" "}
            {balance.isError ? (
              <span className="balance-error">讀取失敗</span>
            ) : bal === null ? (
              "讀取中…"
            ) : (
              <Money value={bal} />
            )}
          </div>
        </div>
        <button type="button" className="btn-ghost" onClick={onClear}>
          取消歸戶
        </button>
      </div>
    );
  }

  return (
    <div className="pos-member">
      <form
        className="pos-member-search"
        onSubmit={(e) => {
          e.preventDefault();
          if (q.trim()) search.mutate(q.trim());
        }}
      >
        <label className="field">
          <span className="field-label">
            會員歸戶（選填；以購物金付款必填）
          </span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="姓名或電話"
            inputMode="text"
          />
        </label>
        <button type="submit" className="btn-ghost" disabled={search.isPending}>
          查詢會員
        </button>
      </form>
      {search.isError && (
        <p role="alert" className="form-error">
          {(search.error as Error).message}
        </p>
      )}
      {search.data && search.data.length === 0 && (
        <p className="hint">查無符合的會員。</p>
      )}
      {search.data && search.data.length > 0 && (
        <ul className="pos-member-results">
          {search.data.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => onSelect(c)}
              >
                {c.name}
                {c.phone ? ` · ${c.phone}` : ""}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
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
}: {
  total: number;
  hasMember: boolean;
  memberBalance: number | null;
  drawerOpen: boolean | null;
  storeCreditMax: number;
  storeCreditMinSpend: number;
  cartHasItems: boolean;
  /** 台灣Pay 手續費率（小數，如 0.02=2%；docs/30）。僅供顯示店家負擔，不向客人收取。 */
  taiwanpayFeePct: number;
  /** LINE Pay 是否啟用（docs/30）：未啟用時不顯示 LINE Pay 收款選項。 */
  linepayEnabled: boolean;
  /** LINE Pay 手續費率（小數）。僅供顯示店家負擔。 */
  linepayFeePct: number;
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
              />
            </label>
            <button
              type="button"
              className="btn-ghost pos-use-max-credit"
              disabled={maxStoreCredit <= 0}
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
            {taiwanpayFeePct > 0 && (
              <>
                {" "}
                · 本筆手續費{" "}
                <Money value={Math.round(plan.taiwanPay * taiwanpayFeePct)} />
                （店家負擔，不向客人收取）
              </>
            )}
          </p>
          <label className="field-toggle pos-payment-confirm">
            <input
              type="checkbox"
              checked={taiwanPayConfirmed}
              onChange={(e) => setTaiwanPayConfirmed(e.target.checked)}
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
            />
          </label>
          <p className="hint">
            LINE Pay 收款 <Money value={plan.linePay} />
            {linepayFeePct > 0 && (
              <>
                {" "}
                · 本筆手續費{" "}
                <Money value={Math.round(plan.linePay * linepayFeePct)} />
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

// 餐飲菜單磚：可售品項一格一格圓角方塊；點磚開數量彈窗。
function MenuPanel({ onAdd }: { onAdd: (line: CartLine) => void }) {
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

export default function PosPage() {
  const queryClient = useQueryClient();
  const [lines, setLines] = useState<CartLine[]>([]);
  const [member, setMember] = useState<ContactRead | null>(null);
  const [mode, setMode] = useState<TenderMode>("CASH");
  const [storeCreditInput, setStoreCreditInput] = useState("");
  const [mixedRemainder, setMixedRemainder] =
    useState<MixedRemainderMethod>("CASH");
  const [taiwanPayConfirmed, setTaiwanPayConfirmed] = useState(false);
  const [receivedInput, setReceivedInput] = useState("");
  // LINE Pay 掃到的客人一次性付款碼（docs/30 P3）；結帳成功後清空、不重用。
  const [linePayKey, setLinePayKey] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [completed, setCompleted] = useState<SaleRead | null>(null);
  // 開錢櫃失敗提示（docs/10 §5：交易已成立，代理離線只提示、不可擋流程）。
  const [drawerNotice, setDrawerNotice] = useState<string | null>(null);
  // 結帳當下生效活動名（供明細聯顯示活動）；結帳成功時自試算結果擷取、清單一變即失效不影響。
  const [completedCampaign, setCompletedCampaign] = useState<string | null>(null);
  const [showDialog, setShowDialog] = useState(false);
  // 購物金扣抵手持簽署（docs/23 K5，D3）：推送至手持裝置後的任務 id；輪詢其狀態，
  // SIGNED 後結帳帶 signature_task_id 綁定（後端驗折抵額精確相符＋單次使用）。
  const [signTaskId, setSignTaskId] = useState<number | null>(null);
  // 完成結帳時綁定的簽署快照（K6 明細聯加印折抵/剩餘＋簽名用；未綁定為 null）。
  const [completedSignature, setCompletedSignature] = useState<CompletedSignature | null>(null);

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/settings");
      if (!data) throw new Error(extractDetail(error) ?? "讀取設定失敗");
      return data;
    },
  });
  const einvoiceEnabled = settings.data?.einvoice_enabled ?? false;

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
  const printProof = useMutation({
    mutationFn: async ({ invoice, sale }: { invoice: InvoiceRead; sale: SaleRead }) => {
      // 抬頭：優先用查詢快取；未就緒/曾失敗 → 即時補抓一次，不因慢載入放棄列印。
      let header = storeHeader.data;
      if (header == null) header = (await storeHeader.refetch()).data ?? undefined;
      if (header?.tax_id == null) throw new Error("讀不到店家統編抬頭");
      await printEInvoice(invoice, sale, { taxId: header.tax_id, name: header.name });
    },
    onSuccess: () => setInvoiceNote("發票已開立，證明聯已送印"),
    onError: (err: Error) =>
      setInvoiceNote(`發票已開立，但證明聯列印失敗：${err.message}（可按重印）`),
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
      setInvoiceNote(`發票尚未開立：${err.message}（銷售已成立，可稍後補開）`);
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
  // 結帳前向後端試算折後總額（docs/21 C2b）：活動生效時 total=折後，收款據此對齊（否則 422）。
  const quote = useQuery({
    queryKey: ["sale-quote", JSON.stringify(saleLines), member?.id ?? null],
    enabled: lines.length > 0,
    queryFn: async () => {
      const { data, error } = await api.POST("/api/v1/sales/quote", {
        body: { lines: saleLines, buyer_contact_id: member?.id ?? null },
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

  // 購物金扣抵手持簽署（docs/23 K5）：輪詢任務狀態；簽署快照的折抵額須與當前收款計畫相符，
  // 改了購物車/收款即失配 → 顯示警告並要求作廢重推（後端結帳時亦精確比對，雙重防線）。
  const signTask = useQuery({
    queryKey: ["signing-task", signTaskId],
    enabled: signTaskId != null,
    refetchInterval: (q) => (q.state.data?.status === "PENDING" ? 2000 : false),
    queryFn: async () => {
      if (signTaskId == null) return null;
      const { data } = await api.GET("/api/v1/signing/tasks/{task_id}", {
        params: { path: { task_id: signTaskId } },
      });
      return data ?? null;
    },
  });
  const signed = signTask.data?.status === "SIGNED";
  const signedDebit =
    signTask.data != null
      ? String((signTask.data.content as Record<string, unknown>).debit ?? "")
      : null;
  const signedTotal =
    signTask.data != null
      ? String((signTask.data.content as Record<string, unknown>).sale_total ?? "")
      : null;
  const signedBalanceBefore =
    signTask.data != null
      ? String((signTask.data.content as Record<string, unknown>).balance_before ?? "")
      : null;
  const signedBalanceAfter =
    signTask.data != null
      ? String((signTask.data.content as Record<string, unknown>).balance_after ?? "")
      : null;
  // 失配＝折抵額/消費合計/餘額快照任一與簽署不符（客人簽的必須就是這筆交易與當下餘額；
  // 後端結帳時亦以帳戶行鎖精確比對——此處為即時 UX 提示）。
  const signMismatch =
    signTaskId != null &&
    (signedDebit !== String(plan.storeCredit) ||
      signedTotal !== String(total) ||
      (memberBalance !== null && signedBalanceBefore !== String(memberBalance)));
  const requireScSigning = settings.data?.require_store_credit_signing === true;
  // 結帳簽署閘門：已推送但未簽/失配 → 擋；政策開啟且以購物金付款而未推送 → 擋。
  const scSignBlock =
    (signTaskId != null && (!signed || signMismatch)) ||
    (requireScSigning && plan.storeCredit > 0 && signTaskId == null);

  const pushSign = useMutation({
    mutationFn: async () => {
      if (!member) throw new Error("請先選擇會員");
      const { data, error } = await api.POST("/api/v1/signing/tasks", {
        body: {
          kind: "STORE_CREDIT_USE",
          contact_id: member.id,
          content: {
            debit: String(plan.storeCredit),
            sale_total: String(total),
          },
          ref_type: "sale",
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "推送手持簽署失敗");
      return data;
    },
    onSuccess: (d) => {
      setNotice(null);
      setSignTaskId(d.id);
    },
    onError: (e: Error) => setNotice(e.message),
  });
  const cancelSign = useMutation({
    mutationFn: async () => {
      if (signTaskId == null) return;
      const { response } = await api.POST("/api/v1/signing/tasks/{task_id}/cancel", {
        params: { path: { task_id: signTaskId } },
      });
      // 非 2xx（如客人剛好已簽 → 409）不可視為取消成功而清除綁定（同 K4）：重新輪詢取回
      // 最新狀態，保留 signTaskId。
      if (!response.ok) {
        await signTask.refetch();
        throw new Error("此簽署已完成或無法取消，請確認手持裝置狀態");
      }
    },
    onSuccess: () => setSignTaskId(null),
    onError: (e: Error) => setNotice(e.message),
  });

  const checkout = useMutation({
    mutationFn: async (): Promise<{ sale: SaleRead; sig: CompletedSignature | null }> => {
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
      const body = {
        lines: toSaleLines(lines),
        buyer_contact_id: member?.id ?? null,
        tenders: toTenders(plan, { linePayKey }) ?? null,
        // 已簽且折抵額相符才綁定（後端亦精確比對＋單次使用守護）。
        signature_task_id: signed && !signMismatch ? signTaskId : null,
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
        lines: canonLines,
        tenders: canonTenders,
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
    },
    onError: (err: Error) => {
      setNotice(err.message);
      // LINE Pay 結帳失敗：一次性付款碼已作廢（單次使用/已過期）→ 清空，提示店員重新掃碼。
      // 冪等鍵已排除付款碼、保持穩定，重掃不會產生新 orderId 重複扣款（見上 sigBody）。
      if (plan.linePay > 0) setLinePayKey("");
    },
  });

  function addToCart(line: CartLine) {
    const result = addLine(lines, line);
    setLines(result.lines);
    setNotice(
      result.duplicateSerialized
        ? `${line.description} 已在購物車（序號品不可重複）`
        : null,
    );
  }

  function resetSale() {
    setLines([]);
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
    setCompletedSignature(null);
    setInvTaxId("");
    setInvBuyerName("");
    setInvCarrier("");
    setInvNpoban("");
    setInvoiceNote(null);
    setCompletedInvoice(null);
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
              <dd>
                {completed.payment_method === "CASH"
                  ? "現金"
                  : completed.payment_method === "STORE_CREDIT"
                    ? "購物金"
                    : completed.payment_method === "TAIWAN_PAY"
                      ? "台灣Pay"
                      : completed.payment_method === "LINE_PAY"
                        ? "LINE Pay"
                        : "混合"}
              </dd>
            </div>
          </dl>
          {drawerNotice !== null && (
            <p role="alert" className="form-error">
              錢櫃未開啟：{drawerNotice}（交易已完成，請以鑰匙開櫃）
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
      <ActiveCampaignBanner />
      <div className="pos-grid">
        <div className="pos-left">
          <ScanBar onResolved={addToCart} />
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
                    const discounted =
                      ql != null &&
                      ql.discount_amount !== "0" &&
                      ql.original_unit_price != null;
                    const unitVal = ql
                      ? (parseNtd(ql.unit_price) ?? line.unitPrice)
                      : line.unitPrice;
                    const subtotalVal = ql
                      ? (parseNtd(ql.line_total) ?? lineTotal(line))
                      : lineTotal(line);
                    const originalUnit =
                      discounted && ql?.original_unit_price != null
                        ? (parseNtd(ql.original_unit_price) ?? line.unitPrice)
                        : null;
                    return (
                      <tr key={line.key}>
                        <td>{line.description}</td>
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
                        <td>
                          <button
                            type="button"
                            className="btn-ghost"
                            aria-label={`移除 ${line.description}`}
                            onClick={() => setLines(removeLine(lines, line.key))}
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
          <MenuPanel onAdd={addToCart} />
        </div>

        <aside className="pos-right card">
          <div className="pos-total">
            <span>應付總額</span>
            <strong>
              <Money value={total} />
            </strong>
          </div>
          {campaignNote && (
            <p className="hint pos-campaign-note">已套用活動折扣：{campaignNote}</p>
          )}
          {lines.length > 0 && quote.isError && (
            <p role="alert" className="form-error">
              試算失敗：{(quote.error as Error).message}
            </p>
          )}

          <MemberPanel
            member={member}
            onSelect={setMember}
            onClear={() => setMember(null)}
          />

          <TenderPanel
            cartHasItems={lines.length > 0}
            total={total}
            hasMember={member !== null}
            memberBalance={memberBalance}
            drawerOpen={drawerOpen}
            storeCreditMax={storeCreditMax}
            storeCreditMinSpend={storeCreditMinSpend}
            taiwanpayFeePct={Number(settings.data?.taiwanpay_fee_pct ?? 0)}
            linepayEnabled={settings.data?.linepay_enabled === true}
            linepayFeePct={Number(settings.data?.linepay_fee_pct ?? 0)}
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
          />

          {/* 購物金扣抵手持簽署（docs/23 K5，D3）：客人於手持端核對折抵/剩餘後手寫簽名 */}
          {plan.storeCredit > 0 && member !== null && (
            <div className="pos-sign-panel">
              <h3>扣抵確認簽署</h3>
              {signTaskId == null ? (
                <>
                  {requireScSigning && (
                    <p className="hint">本店要求購物金扣抵須由客人於手持裝置簽名確認。</p>
                  )}
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={pushSign.isPending || !quoteReady}
                    onClick={() => pushSign.mutate()}
                  >
                    送至手持裝置簽署
                  </button>
                </>
              ) : !signed ? (
                <>
                  <p className="hint">已送至手持裝置，等待客人確認並簽署…</p>
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={cancelSign.isPending}
                    onClick={() => cancelSign.mutate()}
                  >
                    作廢此簽署（重推）
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
                    onClick={() => cancelSign.mutate()}
                  >
                    作廢此簽署（重推）
                  </button>
                </>
              ) : (
                <p className="pos-sign-done">
                  ✓ 客人已完成簽署（折抵 <Money value={plan.storeCredit} />）
                </p>
              )}
            </div>
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
              本期不開票（未啟用電子發票）。
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
              !quoteReady ||
              scSignBlock ||
              invoiceInputBad ||
              // fail-closed（Codex 第十九/二十輪）：結帳需要**新鮮的** settings——
              // pending/失敗、掛載後重抓仍在途（isFetching）、或上次重抓失敗
              // （failureCount>0，快取值可能過期）時 einvoiceEnabled 都不可信：若後端
              // 實際已啟用，結帳會以 invoice:null 開出預設 B2C、不可逆丟失統編/載具/
              // 捐贈選擇。擋結帳待設定讀取成功。
              !settings.isSuccess ||
              settings.isFetching ||
              settings.failureCount > 0
            }
            onClick={() => checkout.mutate()}
          >
            {checkout.isPending
              ? "結帳中…"
              : lines.length > 0 && !quoteReady
                ? "試算中…"
                : scSignBlock && signTaskId != null
                  ? "等待簽署…"
                  : "結帳"}
          </button>
        </aside>
      </div>
    </section>
  );
}
