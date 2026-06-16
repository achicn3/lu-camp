// F6 收購定價輔助純邏輯（docs/10 §/acquisition）：雙重約束的建議最高收購成本、毛利率、
// 建議售價、散裝均一價、應付總額、SPLIT 驗證、購物金溢價試算。無 DOM 依賴 → 可單元測試。
//
// 金額皆正整數元；以 Math.round（正數＝ROUND_HALF_UP，鏡射後端 core/money.round_ntd）收整。
// 這些為「鑑價輔助」估計值，非持久化金額；實際成本/售價由店員輸入。

/** 正數金額收整到整數元（ROUND_HALF_UP）。 */
export function roundNtd(value: number): number {
  return Math.round(value);
}

/** 分類×成色帶定價規則（由 API PricingRuleRead 映射來；min_price_multiple 已 parse 為 number）。 */
export interface PricingRule {
  discountCeilingPct: number;
  minMarginPct: number;
  minPriceMultiple: number;
}

/**
 * 建議最高收購成本（雙重約束取嚴）：
 * - 毛利/折讓：cost ≤ resale × (1 − max(discount_ceiling, min_margin)/100)
 * - 倍數下限：cost ≤ resale ÷ min_price_multiple（救低價品）
 * resale ≤ 0 回 null。
 */
export function maxAcquisitionCost(resaleNtd: number, rule: PricingRule): number | null {
  if (resaleNtd <= 0) return null;
  const ceilingPct = Math.max(rule.discountCeilingPct, rule.minMarginPct);
  const byMargin = resaleNtd * (1 - ceilingPct / 100);
  const byMultiple =
    rule.minPriceMultiple > 0 ? resaleNtd / rule.minPriceMultiple : byMargin;
  return Math.max(0, roundNtd(Math.min(byMargin, byMultiple)));
}

/** 毛利率（整數百分比）= (listed − cost) / listed × 100；listed ≤ 0 回 null。 */
export function marginPct(listedNtd: number, costNtd: number): number | null {
  if (listedNtd <= 0) return null;
  return roundNtd(((listedNtd - costNtd) / listedNtd) * 100);
}

/** 建議含稅售價 = cost ÷ (1 − margin/100)；margin 限 0–99，越界回 null。 */
export function suggestedListedPrice(costNtd: number, targetMarginPct: number): number | null {
  if (targetMarginPct < 0 || targetMarginPct > 99) return null;
  return roundNtd(costNtd / (1 - targetMarginPct / 100));
}

/** 散裝建議每件均一價 = (每件成本) ÷ (1 − margin/100)；qty>0、margin 0–99，否則 null。 */
export function suggestedBulkUnitPrice(
  lotCostNtd: number,
  qty: number,
  targetMarginPct: number,
): number | null {
  if (qty <= 0 || targetMarginPct < 0 || targetMarginPct > 99) return null;
  return roundNtd(lotCostNtd / qty / (1 - targetMarginPct / 100));
}

/** 應付總額 = Σ 成本（買斷各列成本；散裝傳 [整堆成本]）。 */
export function payableTotal(costsNtd: number[]): number {
  return costsNtd.reduce((sum, cost) => sum + cost, 0);
}

/** SPLIT 現金部分合法：整數且 0 < cash < total。 */
export function splitValid(totalNtd: number, cashPartNtd: number): boolean {
  return Number.isInteger(cashPartNtd) && cashPartNtd > 0 && cashPartNtd < totalNtd;
}

/** 購物金溢價試算「可多得」= round_ntd(現金等值 × 溢價率)；premiumRate 為小數（如 0.1）。 */
export function creditPremiumPreview(creditEquivNtd: number, premiumRate: number): number {
  return roundNtd(creditEquivNtd * premiumRate);
}
