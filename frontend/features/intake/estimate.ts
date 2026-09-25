// 收購佇列估價的純計算（docs/42 §4）：與收購頁**同一套**計價與成色推斷，不另寫公式。
// 原價 × 折數 → 預計含稅售價（進位到 10）→ 扣稅、手續費、目標毛利 → 建議收購價；
// 成色沒點時依折數推斷（gradeFromDiscount）。無 DOM 依賴 → 可單元測試。
import {
  NEAR_NEW_DISCOUNT_PCT,
  acquisitionFromListedPrice,
  discountPercent,
  discountedPrice,
  gradeFromDiscount,
  roundUpToListedStep,
} from "@/features/acquisition/pricing";
import { parseNtd } from "@/lib/money";

export interface PricingRates {
  marginPct: number | null;
  taxRate: number | null;
  feeRate: number;
}

/** 設定頁的數字 → 計價用比率；讀不到的稅率回 null（不做換算），手續費讀不到當 0（寧可少補）。 */
export function pricingRates(settings: {
  default_margin_pct: number;
  tax_rate: string;
  linepay_fee_pct: string;
  taiwanpay_fee_pct: string;
} | null | undefined): PricingRates {
  if (!settings) return { marginPct: null, taxRate: null, feeRate: 0 };
  const tax = Number(settings.tax_rate);
  // 行動支付手續費取兩種支付較高者（定價當下不知道客人刷哪一種），同收購頁。
  const fees = [settings.linepay_fee_pct, settings.taiwanpay_fee_pct]
    .map(Number)
    .filter((r) => Number.isFinite(r) && r >= 0 && r < 1);
  return {
    marginPct: settings.default_margin_pct,
    taxRate: Number.isFinite(tax) && tax >= 0 && tax < 1 ? tax : null,
    feeRate: fees.length > 0 ? Math.max(...fees) : 0,
  };
}

export interface LineEstimate {
  /** 預計含稅售價／件（進位到 10）。 */
  listed: number | null;
  /** 建議收購價／件。 */
  suggestedCost: number | null;
  /** 成色沒點時的推斷。 */
  inferredGrade: "S" | "A" | "B" | "C" | null;
  /** 折數 ≥ 六折：可能是新品，要提醒確認成色。 */
  nearNew: boolean;
}

export function estimateLine(reference: string, discount: string, rates: PricingRates): LineEstimate {
  const ref = parseNtd(reference);
  const listed = ref === null ? null : roundUpToListedStep(discountedPrice(ref, discount));
  const suggestedCost =
    listed === null || rates.marginPct === null || rates.taxRate === null
      ? null
      : acquisitionFromListedPrice(listed, rates.marginPct, rates.taxRate, rates.feeRate);
  return {
    listed,
    suggestedCost,
    inferredGrade: gradeFromDiscount(discount),
    nearNew: (discountPercent(discount) ?? 0) >= NEAR_NEW_DISCOUNT_PCT,
  };
}

/** 折數字串 → API 的十分位整數（6.5 → 65）；不合法回 null。 */
export function discountToPct(discount: string): number | null {
  return discountPercent(discount);
}

/** API 的十分位整數 → 折數字串（65 → "6.5"、50 → "5"）。 */
export function pctToDiscount(pct: number | null | undefined): string {
  if (pct == null) return "";
  return pct % 10 === 0 ? String(pct / 10) : (pct / 10).toFixed(1);
}
