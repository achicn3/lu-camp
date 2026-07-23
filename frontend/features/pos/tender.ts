// POS 收款純邏輯（docs/16 §3.2）：現金 / 購物金 / 混合。金額整數元。
// 後端規則：Σ tenders = total；購物金扣買方餘額（不足 409）；純購物金不需開帳。
import type { components } from "@/lib/api-types";

export type TenderMode =
  | "CASH"
  | "STORE_CREDIT"
  | "TAIWAN_PAY"
  | "LINE_PAY"
  | "MIXED";

export type MixedRemainderMethod = "CASH" | "LINE_PAY" | "TAIWAN_PAY";

export interface TenderPlan {
  mode: TenderMode;
  /** 現金部分（MIXED+CASH 時 = total − 購物金；CASH 時 = total；其餘 0）。 */
  cash: number;
  /** 購物金部分（MIXED 時為使用者輸入；STORE_CREDIT 時 = total；其餘 0）。 */
  storeCredit: number;
  /** 台灣Pay 部分（docs/30）：TAIWAN_PAY 時 = total；其餘 0。非現金、不進抽屜、不需會員。 */
  taiwanPay: number;
  /** LINE Pay 部分（docs/30）：LINE_PAY 時 = total；其餘 0。非現金、需掃客人一次性付款碼。 */
  linePay: number;
}

export interface TenderValidation {
  ok: boolean;
  /** 不可結帳時的中文原因（顯示於收款區）。 */
  error: string | null;
  /** 是否需要買方會員（含購物金時為真）。 */
  needsMember: boolean;
  /** 是否需要開帳（含現金時為真）。 */
  needsDrawer: boolean;
}

/** 依模式拆分 total；MIXED 輸入購物金，其餘金額交由選定的單一付款方式。 */
export function resolvePlan(
  mode: TenderMode,
  total: number,
  storeCreditInput: number,
  mixedRemainder: MixedRemainderMethod = "CASH",
): TenderPlan {
  if (mode === "CASH")
    return { mode, cash: total, storeCredit: 0, taiwanPay: 0, linePay: 0 };
  if (mode === "STORE_CREDIT")
    return { mode, cash: 0, storeCredit: total, taiwanPay: 0, linePay: 0 };
  if (mode === "TAIWAN_PAY")
    return { mode, cash: 0, storeCredit: 0, taiwanPay: total, linePay: 0 };
  if (mode === "LINE_PAY")
    return { mode, cash: 0, storeCredit: 0, taiwanPay: 0, linePay: total };
  const storeCredit = clampInt(storeCreditInput);
  const remainder = total - storeCredit;
  return {
    mode,
    cash: mixedRemainder === "CASH" ? remainder : 0,
    storeCredit,
    taiwanPay: mixedRemainder === "TAIWAN_PAY" ? remainder : 0,
    linePay: mixedRemainder === "LINE_PAY" ? remainder : 0,
  };
}

export function validatePlan(
  plan: TenderPlan,
  total: number,
  opts: {
    hasMember: boolean;
    memberBalance: number | null;
    /** 是否開帳中（含現金收款必須開帳，§7.8）；null = 讀取中/未知。 */
    drawerOpen: boolean | null;
    /** 購物金可折抵上限（=total−餐飲小計，內用不得以購物金折抵；來自 /sales/quote）。 */
    storeCreditMax?: number;
    /** 購物金低消門檻（非餐飲消費未達則完全不可用購物金；0＝不限；來自 /sales/quote）。 */
    storeCreditMinSpend?: number;
    /** 購物車是否有品項：區分「空車初始狀態」與「非空但折後總額為 0」（Codex 波次三 P2）。 */
    cartHasItems?: boolean;
    /** LINE Pay 掃到的客人一次性付款碼（docs/30）：LINE_PAY 付款時必填，空則擋。 */
    linePayKey?: string;
    /** 台灣Pay 無 API；店員須明確確認 App 已收到本次應收金額。 */
    taiwanPayConfirmed?: boolean;
  },
): TenderValidation {
  const needsMember = plan.storeCredit > 0;
  const needsDrawer = plan.cash > 0;
  if (total <= 0) {
    // 非空購物車卻折後總額為 0（如 $1 商品套 99% 活動折扣、後端捨入為 0）：不可靜默停用，
    // 給店員可行動的說明。真空車（無品項）才是中性初始狀態、不回錯誤（結帳鈕仍由 !ok 停用）。
    if (opts.cartHasItems) {
      return {
        ok: false,
        error: "折後總額為 0，無法結帳（請確認活動折扣或商品價格）",
        needsMember,
        needsDrawer,
      };
    }
    return { ok: false, error: null, needsMember, needsDrawer };
  }
  if (plan.mode === "MIXED") {
    const remainderLegs = [plan.cash, plan.taiwanPay, plan.linePay];
    if (
      plan.storeCredit <= 0 ||
      remainderLegs.filter((amount) => amount > 0).length !== 1 ||
      remainderLegs.some((amount) => amount < 0)
    ) {
      return {
        ok: false,
        error: "混合付款的購物金與剩餘付款都必須大於 0，且只能選一種剩餘付款方式",
        needsMember,
        needsDrawer,
      };
    }
  }
  if (plan.cash + plan.storeCredit + plan.taiwanPay + plan.linePay !== total) {
    return {
      ok: false,
      error: "收款金額必須等於應付總額",
      needsMember,
      needsDrawer,
    };
  }
  // LINE Pay 需先掃到客人的一次性付款碼才能結帳（避免送出才被後端 422/402）。
  if (plan.linePay > 0 && !(opts.linePayKey && opts.linePayKey.trim())) {
    return {
      ok: false,
      error: "請先掃描客人的 LINE Pay 付款條碼",
      needsMember,
      needsDrawer,
    };
  }
  if (plan.taiwanPay > 0 && opts.taiwanPayConfirmed !== true) {
    return {
      ok: false,
      error: `請確認已於台灣Pay收到 ${plan.taiwanPay} 元`,
      needsMember,
      needsDrawer,
    };
  }
  if (needsMember && !opts.hasMember) {
    return {
      ok: false,
      error: "以購物金付款必須先指定買方會員",
      needsMember,
      needsDrawer,
    };
  }
  // 購物金餘額未載入（查詢中或失敗）→ 不放行（不可在不知餘額時就准許扣抵）。
  if (needsMember && opts.memberBalance === null) {
    return {
      ok: false,
      error: "購物金餘額尚未載入，請稍候或重試",
      needsMember,
      needsDrawer,
    };
  }
  if (
    needsMember &&
    opts.memberBalance !== null &&
    plan.storeCredit > opts.memberBalance
  ) {
    return { ok: false, error: "購物金餘額不足", needsMember, needsDrawer };
  }
  // 購物金低消門檻（彈性設定，0＝不限）：非餐飲消費（=storeCreditMax）未達門檻則完全不可用購物金。
  if (
    needsMember &&
    opts.storeCreditMinSpend !== undefined &&
    opts.storeCreditMinSpend > 0 &&
    opts.storeCreditMax !== undefined &&
    opts.storeCreditMax < opts.storeCreditMinSpend
  ) {
    return {
      ok: false,
      error: `未達購物金低消：非餐飲消費需滿 ${opts.storeCreditMinSpend} 元才能折抵購物金`,
      needsMember,
      needsDrawer,
    };
  }
  // 內用餐飲不得以購物金折抵（與後端 M1 不變量一致）：購物金 ≤ total−餐飲小計。
  if (
    needsMember &&
    opts.storeCreditMax !== undefined &&
    plan.storeCredit > opts.storeCreditMax
  ) {
    return {
      ok: false,
      error: `內用餐飲不可用購物金折抵（購物金最多 ${opts.storeCreditMax} 元）`,
      needsMember,
      needsDrawer,
    };
  }
  // 含現金收款必須開帳中（§7.8）：未知（讀取中）或未開帳都不放行，避免送出才吃 409。
  if (needsDrawer && opts.drawerOpen !== true) {
    return {
      ok: false,
      error:
        opts.drawerOpen === null
          ? "讀取開帳狀態中…"
          : "現金結帳需先開帳（請至現金對帳開帳）",
      needsMember,
      needsDrawer,
    };
  }
  return { ok: true, error: null, needsMember, needsDrawer };
}

/** 轉成 POST /sales 的 tenders payload；省略（純現金且未指定）時回 undefined 走後端預設。
 * LINE Pay 需帶掃到的一次性付款碼（oneTimeKey），由 opts.linePayKey 傳入。 */
export function toTenders(
  plan: TenderPlan,
  opts: { linePayKey?: string } = {},
): components["schemas"]["SaleTenderRequest"][] | undefined {
  const tenders: components["schemas"]["SaleTenderRequest"][] = [];
  if (plan.cash > 0)
    tenders.push({ tender_type: "CASH", amount: String(plan.cash) });
  if (plan.storeCredit > 0)
    tenders.push({
      tender_type: "STORE_CREDIT",
      amount: String(plan.storeCredit),
    });
  if (plan.taiwanPay > 0)
    tenders.push({ tender_type: "TAIWAN_PAY", amount: String(plan.taiwanPay) });
  if (plan.linePay > 0)
    tenders.push({
      tender_type: "LINE_PAY",
      amount: String(plan.linePay),
      line_pay_one_time_key: opts.linePayKey?.trim() ?? null,
    });
  return tenders.length > 0 ? tenders : undefined;
}

/** 收銀台找零輔助：實收現金 − 應收現金部分（負數代表不足，不影響貼到後端的 tender）。 */
export function changeDue(received: number, cashPart: number): number {
  return received - cashPart;
}

function clampInt(n: number): number {
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}
