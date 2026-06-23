// POS 收款純邏輯（docs/16 §3.2）：現金 / 購物金折抵（可部分、可全額）。金額整數元。
// 後端規則：Σ tenders = total；購物金扣買方餘額（不足 409）；無現金腿時不需開帳。
import type { components } from "@/lib/api-types";

export type TenderMode = "CASH" | "CREDIT";

export interface TenderPlan {
  mode: TenderMode;
  /** 現金部分（CASH 時 = total；CREDIT 時 = total − 購物金，可為 0）。 */
  cash: number;
  /** 購物金部分（CASH 時 0；CREDIT 時 = 使用者輸入的折抵金額）。 */
  storeCredit: number;
}

export interface TenderValidation {
  ok: boolean;
  /** 不可結帳時的中文原因（顯示於收款區）。 */
  error: string | null;
  /** 是否需要買方會員（用到購物金時為真）。 */
  needsMember: boolean;
  /** 是否需要開帳（有現金腿時為真）。 */
  needsDrawer: boolean;
}

/**
 * 依模式把 total 拆成現金/購物金兩腿。
 * CREDIT 模式以「購物金折抵金額」為輸入，現金自動補足餘額（cash = total − 購物金）；
 * 折抵滿額時現金腿為 0（等同純購物金付款）。
 */
export function resolvePlan(
  mode: TenderMode,
  total: number,
  creditInput: number,
): TenderPlan {
  if (mode === "CASH") return { mode, cash: total, storeCredit: 0 };
  const storeCredit = clampInt(creditInput);
  return { mode, cash: total - storeCredit, storeCredit };
}

/**
 * 全額折抵可帶入的購物金金額：受 會員餘額、可折抵上限（內用排除）、應付總額 三者夾擠取最小。
 * 無會員（餘額 null）→ 0。供「全額折抵」按鈕與前端防呆共用。
 */
export function maxRedeemable(
  total: number,
  memberBalance: number | null,
  storeCreditMax: number,
): number {
  const balance = memberBalance ?? 0;
  return Math.max(0, Math.min(clampInt(total), balance, clampInt(storeCreditMax)));
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
  },
): TenderValidation {
  const needsMember = plan.storeCredit > 0;
  const needsDrawer = plan.cash > 0;
  if (total <= 0) {
    return { ok: false, error: "購物車是空的", needsMember, needsDrawer };
  }
  // 購物金折抵模式卻未輸入折抵金額（預設 0）→ 提示輸入或改用現金。
  if (plan.mode === "CREDIT" && plan.storeCredit <= 0) {
    return {
      ok: false,
      error: "請輸入購物金折抵金額（或改用現金付款）",
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
  // 防呆：折抵金額不得大於會員購物金餘額（前端先擋，後端 InsufficientStoreCredit 為最終把關）。
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
    const msg =
      opts.storeCreditMax < total
        ? `內用餐飲不可用購物金折抵（購物金最多 ${opts.storeCreditMax} 元）`
        : `購物金折抵最多 ${opts.storeCreditMax} 元`;
    return { ok: false, error: msg, needsMember, needsDrawer };
  }
  if (plan.cash + plan.storeCredit !== total) {
    return {
      ok: false,
      error: "收款金額必須等於應付總額",
      needsMember,
      needsDrawer,
    };
  }
  // 有現金腿必須開帳中（§7.8）：未知（讀取中）或未開帳都不放行，避免送出才吃 409。
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
