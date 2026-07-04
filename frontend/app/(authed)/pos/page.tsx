"use client";
// /pos 結帳（docs/10 §5、docs/16 §3.2）：掃碼加入購物車（序號品／散裝堆）→ 會員歸戶（選填）
// → 收款（現金／購物金／混合）→ 結帳 POST /sales →（完成後）詢問是否列印商品明細。
// einvoice_enabled=false 時發票區隱藏（顯示「本期不開票」），載具輸入待啟用後再開。
import { useMutation, useQuery } from "@tanstack/react-query";
import { type ChangeEvent, type FormEvent, useRef, useState } from "react";

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
  type TenderMode,
  changeDue,
  resolvePlan,
  toTenders,
  validatePlan,
} from "@/features/pos/tender";
import { openCashDrawer, printSaleDetail } from "@/lib/agent";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";
import { newIdempotencyKey } from "@/lib/uuid";

type SaleRead = components["schemas"]["SaleRead"];
type ContactRead = components["schemas"]["ContactRead"];
type CampaignRead = components["schemas"]["CampaignRead"];
type MenuItemRead = components["schemas"]["MenuItemRead"];

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
// 數量型商品以 SKU 查（任意字串，掃碼槍尾端 Enter 送出）：序號品 → 散裝 → 數量品 一格通吃。
const ITEM_CODE_RE = /^[SL]\d+-[0-9A-F]{10}$/;

function ScanBar({ onResolved }: { onResolved: (line: CartLine) => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async (code: string): Promise<CartLine> => {
      // 先試序號品，再試散裝堆，最後試數量品 SKU（一格掃碼通吃，docs/10 §3）。
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
      // 最後試數量型商品（SKU）：廠商採購品（瓦斯罐/糧食等）在 POS 直接掃售。
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
  mode,
  setMode,
  cashInput,
  setCashInput,
  receivedInput,
  setReceivedInput,
}: {
  total: number;
  hasMember: boolean;
  memberBalance: number | null;
  drawerOpen: boolean | null;
  storeCreditMax: number;
  storeCreditMinSpend: number;
  mode: TenderMode;
  setMode: (m: TenderMode) => void;
  cashInput: string;
  setCashInput: (v: string) => void;
  receivedInput: string;
  setReceivedInput: (v: string) => void;
}) {
  const plan = resolvePlan(mode, total, parseNtd(cashInput) ?? 0);
  const validation = validatePlan(plan, total, {
    hasMember,
    memberBalance,
    drawerOpen,
    storeCreditMax,
    storeCreditMinSpend,
  });
  const received = parseNtd(receivedInput);
  const change = received !== null ? changeDue(received, plan.cash) : null;
  return (
    <div className="pos-tender">
      <div className="pos-tender-modes" role="radiogroup" aria-label="收款方式">
        {(["CASH", "STORE_CREDIT", "MIXED"] as const).map((m) => (
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
            {m === "CASH" ? "現金" : m === "STORE_CREDIT" ? "購物金" : "混合"}
          </label>
        ))}
      </div>

      {mode === "MIXED" && (
        <label className="field">
          <span className="field-label">現金部分（其餘以購物金支付）</span>
          <input
            value={cashInput}
            onChange={(e) => setCashInput(e.target.value)}
            inputMode="numeric"
          />
        </label>
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
function PrintDialog({
  sale,
  campaignName,
  onClose,
}: {
  sale: SaleRead;
  campaignName: string | null;
  onClose: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [printed, setPrinted] = useState(false);
  const print = useMutation({
    mutationFn: async () => {
      // 1) 實體列印：把 SaleRead（含折扣留痕）轉送硬體代理 → EPSON 印明細聯。
      await printSaleDetail(sale, campaignName);
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
          完成結帳。可現在列印商品明細聯，或日後在交易紀錄補印。
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
  const [lines, setLines] = useState<CartLine[]>([]);
  const [member, setMember] = useState<ContactRead | null>(null);
  const [mode, setMode] = useState<TenderMode>("CASH");
  const [cashInput, setCashInput] = useState("");
  const [receivedInput, setReceivedInput] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [completed, setCompleted] = useState<SaleRead | null>(null);
  // 開錢櫃失敗提示（docs/10 §5：交易已成立，代理離線只提示、不可擋流程）。
  const [drawerNotice, setDrawerNotice] = useState<string | null>(null);
  // 結帳當下生效活動名（供明細聯顯示活動）；結帳成功時自試算結果擷取、清單一變即失效不影響。
  const [completedCampaign, setCompletedCampaign] = useState<string | null>(null);
  const [showDialog, setShowDialog] = useState(false);
  // 冪等鍵：以送出內容簽章決定是否換鍵（見 checkout）；放 ref 不觸發 render。
  const idemRef = useRef<{ sig: string; key: string }>({
    sig: "",
    key: newIdempotencyKey(),
  });

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/settings");
      if (!data) throw new Error(extractDetail(error) ?? "讀取設定失敗");
      return data;
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
  const plan = resolvePlan(mode, total, parseNtd(cashInput) ?? 0);
  const validation = validatePlan(plan, total, {
    hasMember: member !== null,
    memberBalance,
    drawerOpen,
    storeCreditMax,
    storeCreditMinSpend,
  });

  const checkout = useMutation({
    mutationFn: async (): Promise<SaleRead> => {
      const body = {
        lines: toSaleLines(lines),
        buyer_contact_id: member?.id ?? null,
        tenders: toTenders(plan) ?? null,
      };
      // 冪等鍵綁定送出內容（Codex F3 P2）：同 payload 的網路重試沿用同鍵（後端冪等回原單）；
      // 改了購物車/會員/收款再送則換新鍵，不會被「同鍵不同內容」的 409 卡死。
      const sig = JSON.stringify(body);
      if (sig !== idemRef.current.sig) {
        idemRef.current = { sig, key: newIdempotencyKey() };
      }
      const { data, error } = await api.POST("/api/v1/sales", {
        params: { header: { "Idempotency-Key": idemRef.current.key } },
        body,
      });
      if (!data) throw new Error(extractDetail(error) ?? "結帳失敗");
      return data;
    },
    onSuccess: (sale) => {
      setCompleted(sale);
      setCompletedCampaign(campaignNote);
      setShowDialog(true);
      // 收現才開錢櫃（docs/10 §5）；純購物金不碰現金、不開櫃。
      // fire-and-forget：交易已寫後端，開櫃失敗只在完成畫面提示、不擋流程。
      if (plan.cash > 0) {
        setDrawerNotice(null);
        openCashDrawer().catch((err: Error) => setDrawerNotice(err.message));
      }
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
    setCashInput("");
    setReceivedInput("");
    setNotice(null);
    setCompleted(null);
    setCompletedCampaign(null);
    setShowDialog(false);
    setDrawerNotice(null);
    idemRef.current = { sig: "", key: newIdempotencyKey() };
    checkout.reset();
  }

  // 完成畫面（結帳成功後）
  if (completed !== null) {
    return (
      <section>
        <h1 className="page-title">POS 結帳</h1>
        <div className="card pos-complete">
          <h2>
            已完成 <span className="badge-open">#{completed.id}</span>
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
                    : "混合"}
              </dd>
            </div>
          </dl>
          {drawerNotice !== null && (
            <p role="alert" className="form-error">
              錢櫃未開啟：{drawerNotice}（交易已完成，請以鑰匙開櫃）
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
            onClose={() => setShowDialog(false)}
          />
        )}
      </section>
    );
  }

  const einvoiceEnabled = settings.data?.einvoice_enabled ?? false;

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
            total={total}
            hasMember={member !== null}
            memberBalance={memberBalance}
            drawerOpen={drawerOpen}
            storeCreditMax={storeCreditMax}
            storeCreditMinSpend={storeCreditMinSpend}
            mode={mode}
            setMode={setMode}
            cashInput={cashInput}
            setCashInput={setCashInput}
            receivedInput={receivedInput}
            setReceivedInput={setReceivedInput}
          />

          {/* 發票區（docs/10 §5/§6）：讀不到設定時不可逕自當「不開票」（Codex F3 P3）。 */}
          {settings.isError ? (
            <p role="alert" className="form-error pos-invoice-off">
              無法讀取發票設定，請重試。
            </p>
          ) : settings.isPending ? (
            <p className="hint pos-invoice-off">讀取發票設定中…</p>
          ) : einvoiceEnabled ? (
            // 版面預留載具輸入，但目前後端 /sales 尚不收發票/載具欄位（電子發票於
            // T13/T14 開立）。停用此欄並明示，避免「看似已帶入卻被靜默丟棄」（Codex F3 P2）。
            <label className="field pos-invoice-off">
              <span className="field-label">
                雲端發票載具（掃描手機條碼，8 碼 / 開頭）
              </span>
              <input
                name="carrier"
                inputMode="text"
                placeholder="/XXXXXXX"
                disabled
              />
              <span className="hint">
                電子發票開立尚未上線（T13/T14），暫不帶入載具。
              </span>
            </label>
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
            disabled={!validation.ok || checkout.isPending || !quoteReady}
            onClick={() => checkout.mutate()}
          >
            {checkout.isPending
              ? "結帳中…"
              : lines.length > 0 && !quoteReady
                ? "試算中…"
                : "結帳"}
          </button>
        </aside>
      </div>
    </section>
  );
}
