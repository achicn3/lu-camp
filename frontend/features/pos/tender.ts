// POS 收款純邏輯（docs/16 §3.2）：現金 / 購物金 / 混合。金額整數元。
// 後端規則：Σ tenders = total；購物金扣買方餘額（不足 409）；純購物金不需開帳。
import type { components } from "@/lib/api-types";

export type TenderMode = "CASH" | "STORE_CREDIT" | "MIXED";

export interface TenderPlan {
  mode: TenderMode;
  /** 現金部分（MIXED 時為使用者輸入；CASH 時 = total；STORE_CREDIT 時 0）。 */
  cash: number;
  /** 購物金部分（MIXED 時 = total − cash；STORE_CREDIT 時 = total；CASH 時 0）。 */
  storeCredit: number;
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

/** 依模式把 total 拆成現金/購物金兩腿（MIXED 用使用者輸入的現金部分）。 */
export function resolvePlan(
  mode: TenderMode,
  total: number,
  cashInput: number,
): TenderPlan {
  if (mode === "CASH") return { mode, cash: total, storeCredit: 0 };
  if (mode === "STORE_CREDIT") return { mode, cash: 0, storeCredit: total };
  const cash = clampInt(cashInput);
  return { mode, cash, storeCredit: total - cash };
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
  },
): TenderValidation {
  const needsMember = plan.storeCredit > 0;
  const needsDrawer = plan.cash > 0;
  if (total <= 0) {
    return { ok: false, error: "購物車是空的", needsMember, needsDrawer };
  }
  if (plan.mode === "MIXED") {
    if (plan.cash <= 0 || plan.storeCredit <= 0) {
      return {
        ok: false,
        error: "混合付款的現金與購物金都必須大於 0",
        needsMember,
        needsDrawer,
      };
    }
  }
  if (plan.cash + plan.storeCredit !== total) {
    return {
      ok: false,
      error: "收款金額必須等於應付總額",
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

/** 轉成 POST /sales 的 tenders payload；省略（純現金且未指定）時回 undefined 走後端預設。 */
export function toTenders(
  plan: TenderPlan,
): components["schemas"]["SaleTenderRequest"][] | undefined {
  const tenders: components["schemas"]["SaleTenderRequest"][] = [];
  if (plan.cash > 0)
    tenders.push({ tender_type: "CASH", amount: String(plan.cash) });
  if (plan.storeCredit > 0)
    tenders.push({
      tender_type: "STORE_CREDIT",
      amount: String(plan.storeCredit),
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
