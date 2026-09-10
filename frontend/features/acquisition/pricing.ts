// F6 收購定價輔助純邏輯（docs/10 §/acquisition）：雙重約束的建議最高收購成本、毛利率、
// 建議售價、散裝均一價、應付總額、SPLIT 驗證、購物金溢價試算。無 DOM 依賴 → 可單元測試。
//
// 金額皆正整數元；一律以 ROUND_HALF_UP 鏡射後端 core/money.round_ntd，可能超出
// Number 安全整數的中間乘法改用 BigInt 整數比值收整。
// 這些為「鑑價輔助」估計值，非持久化金額；實際成本/售價由店員輸入。

import { roundNtdByRate } from "@/lib/money";

const BASIS_POINTS_PER_UNIT = 10_000;
const PERCENT_POINTS_PER_UNIT = 100;
const ROUND_HALF_UP_FACTOR = BigInt(2);

/** 正數金額收整到整數元（ROUND_HALF_UP）。 */
export function roundNtd(value: number): number {
  return Math.round(value);
}

/**
 * 稅率轉基點整數（如 0.05 → 500）。
 *
 * 稅率最多四位小數（DB Numeric(5,4)），先轉整數基點，讓後續換算都能用 BigInt
 * 整數比值完成，避免 Numeric(12,0) 合法大額的中間乘法超出安全整數。
 */
function rateBasisPoints(taxRate: number): number {
  return Math.round(taxRate * BASIS_POINTS_PER_UNIT);
}

/** 整數比值 ROUND_HALF_UP；BigInt 避免合法 Numeric(12,0) 的中間乘法失真。 */
function roundRatio(numerator: bigint, denominator: bigint): number {
  const negative = numerator < BigInt(0);
  const magnitude = negative ? -numerator : numerator;
  const rounded =
    (ROUND_HALF_UP_FACTOR * magnitude + denominator) /
    (ROUND_HALF_UP_FACTOR * denominator);
  return Number(negative ? -rounded : rounded);
}

/**
 * 未稅 → 含稅（整數元）。稅率由 settings 提供，不得寫死（CLAUDE.md §6）。
 *
 * 台灣標價含稅：店員心裡的「這件我要賣 2010」是**未稅**（他實際要拿到的錢），
 * 掛在架上讓客人看的是含稅 2111。少了這一步，客人付 2010、店家只實得 1914。
 */
export function taxInclusivePrice(netNtd: number, taxRate: number): number | null {
  if (netNtd <= 0) return null;
  const numerator =
    BigInt(netNtd) * BigInt(BASIS_POINTS_PER_UNIT + rateBasisPoints(taxRate));
  return roundRatio(numerator, BigInt(BASIS_POINTS_PER_UNIT));
}

/**
 * 未稅 → **含稅且含行動支付手續費**（整數元）。
 *
 *     含稅含費 = round(未稅 × (1 + 稅率) ÷ (1 − 費率 × (1 + 稅率)))
 *
 * 手續費是店家被金流商抽走的錢，不補進標價目標毛利就達不到（裁示 2026-09-09）。
 * 除以 (1 − 費率×(1+稅率)) 是**精確補償**：讓扣掉手續費後的未稅實得正好等於原本
 * 那個未稅數字；乘以 (1+費率) 則會補償不足。費率 0 時退化為純加稅。
 */
export function taxAndFeeInclusivePrice(
  netNtd: number,
  taxRate: number,
  feeRate: number,
): number | null {
  if (netNtd <= 0 || feeRate < 0) return null;
  const taxed = BASIS_POINTS_PER_UNIT + rateBasisPoints(taxRate);
  const feeDivisor =
    BigInt(BASIS_POINTS_PER_UNIT) * BigInt(BASIS_POINTS_PER_UNIT) -
    BigInt(rateBasisPoints(feeRate)) * BigInt(taxed);
  if (feeDivisor <= BigInt(0)) return null;
  const numerator = BigInt(netNtd) * BigInt(taxed) * BigInt(BASIS_POINTS_PER_UNIT);
  return roundRatio(numerator, feeDivisor);
}

/** 含稅 → 未稅（整數元）；與後端 `core/money.split_tax_inclusive` 同式：round(total / (1+rate))。 */
export function netOfTaxInclusive(grossNtd: number, taxRate: number): number {
  const numerator = BigInt(grossNtd) * BigInt(BASIS_POINTS_PER_UNIT);
  const denominator = BigInt(BASIS_POINTS_PER_UNIT + rateBasisPoints(taxRate));
  return roundRatio(numerator, denominator);
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
  const byMargin = resaleNtd * (1 - ceilingPct / PERCENT_POINTS_PER_UNIT);
  const byMultiple =
    rule.minPriceMultiple > 0 ? resaleNtd / rule.minPriceMultiple : byMargin;
  return Math.max(0, roundNtd(Math.min(byMargin, byMultiple)));
}

/**
 * 毛利率（整數百分比），**以未稅售價為分母**：(未稅 − cost) / 未稅 × 100。
 *
 * 用含稅售價當分母會系統性高估——那 5% 是代政府收的，從來不是店家的毛利。
 * （含稅 2010、成本 1000：舊寫法顯示 50%，實際只有 48%。）
 * `listedTaxInclusiveNtd` ≤ 0 或未稅算出來 ≤ 0 回 null。
 */
export function marginPct(
  listedTaxInclusiveNtd: number,
  costNtd: number,
  taxRate: number,
  feeRate = 0,
): number | null {
  if (listedTaxInclusiveNtd <= 0 || feeRate < 0) return null;
  // 手續費也要扣：標價現在含手續費（裁示 2026-09-09），只還原稅、不扣費的話
  // 分母把被金流商抽走的錢也算成店家的，毛利會系統性高估（1954/1000：46% vs 45%）。
  const fee = roundNtd(listedTaxInclusiveNtd * feeRate);
  const net = netOfTaxInclusive(listedTaxInclusiveNtd, taxRate) - fee;
  if (net <= 0) return null;
  const numerator = BigInt(net - costNtd) * BigInt(PERCENT_POINTS_PER_UNIT);
  return roundRatio(numerator, BigInt(net));
}

/**
 * 建議**含稅**售價；margin 限 0–99，越界回 null。與後端 core/money.suggested_price 同式。
 *
 *     未稅目標 = cost ÷ (1 − margin/100)
 *     含稅售價 = round(未稅目標 × (1 + 稅率) ÷ (1 − 費率 × (1 + 稅率)))
 *
 * 目標毛利是對**未稅**談的，所以先算未稅售價、最後才加稅。**只四捨五入一次**：
 * 先把未稅取整再加稅會多一次捨入誤差。
 *
 * `feeRate` 是行動支付手續費（兩種支付取較高者），店家被金流商抽走、不向客人加收。
 * 不補進標價目標毛利就達不到：收 1000／毛利 45%／稅 5% 的舊式建議價 1909，客人刷
 * 行動支付被抽 42 元後未稅實得只剩 1776，實際毛利 43.7%。
 *
 * 採**精確補償**（除以 1 − 費率×(1+稅率)）而不是把費率乘上去——後者補償不足。
 * 費率 0 時退化為舊式。費率×(1+稅率) ≥ 1（手續費把售價吃光）或負費率回 null。
 */
export function suggestedListedPrice(
  costNtd: number,
  targetMarginPct: number,
  taxRate: number,
  feeRate = 0,
): number | null {
  if (targetMarginPct < 0 || targetMarginPct > 99) return null;
  if (feeRate < 0) return null;
  // 全程整數比值，中途不落到浮點：`cost / (1 − m/100)` 這半原本是浮點除法，
  // 剛好落在 .5 的商會算成 …49999999 而少一元（實測與後端在 2.76% 的輸入上不一致，
  // 例：cost 87、margin 10、稅率 5% → 後端 102、前端 101）。
  //   未稅 = cost × 100 ÷ (100 − m)；含稅 = 未稅 × (10000 + bp) ÷ 10000
  //   未稅 = cost × 100 ÷ (100 − m)
  //   含稅 = 未稅 × (10000 + 稅bp) ÷ 10000
  //   補手續費 = ÷ (1 − 費bp/10000 × (10000 + 稅bp)/10000)
  //           = × 10^8 ÷ (10^8 − 費bp × (10000 + 稅bp))
  // 三者合併後 10000 對消掉一組，全程整數比值、中途不落浮點。
  const taxed = BASIS_POINTS_PER_UNIT + rateBasisPoints(taxRate);
  const feeBp = rateBasisPoints(feeRate);
  const feeDivisor =
    BigInt(BASIS_POINTS_PER_UNIT) * BigInt(BASIS_POINTS_PER_UNIT) - BigInt(feeBp) * BigInt(taxed);
  // 手續費×(1+稅率) ≥ 1 → 分母 ≤ 0，是無意義的設定，回 null 而不是算出怪數字。
  if (feeDivisor <= BigInt(0)) return null;
  const numerator =
    BigInt(costNtd) *
    BigInt(PERCENT_POINTS_PER_UNIT) *
    BigInt(taxed) *
    BigInt(BASIS_POINTS_PER_UNIT);
  const denominator = BigInt(PERCENT_POINTS_PER_UNIT - targetMarginPct) * feeDivisor;
  // 正數有理數的 ROUND_HALF_UP：floor(n/d + 1/2)。BigInt 避免 Numeric(12,0)
  // 合法大額在中間乘法超出 Number.MAX_SAFE_INTEGER 後掉一元。
  return roundRatio(numerator, denominator);
}

/** SPLIT 現金部分合法：整數且 0 < cash < total。 */
export function splitValid(totalNtd: number, cashPartNtd: number): boolean {
  return Number.isInteger(cashPartNtd) && cashPartNtd > 0 && cashPartNtd < totalNtd;
}

/** 購物金溢價試算「可多得」= round_ntd(現金等值 × API Decimal 溢價率字串)。 */
export function creditPremiumPreview(creditEquivNtd: number, premiumRate: string): number {
  return roundNtdByRate(creditEquivNtd, premiumRate) ?? 0;
}
