// 收購選購物金的加碼（後端寫在切結內容的 store_credit_premium.rate，例如 "0.03"）。
// 給客人看的字一律寫「多拿幾 %」，不寫多得多少錢（店主 2026-10-03）。

/** 加碼比率 → 「3」「2.5」這樣的百分比字串；沒有加碼或讀不懂回 null（不顯示）。 */
export function premiumPercent(rate: unknown): string | null {
  const value = Number(rate);
  if (!Number.isFinite(value) || value <= 0) return null;
  return `${Math.round(value * 1000) / 10}`;
}

/** 給客人看的加碼字樣；沒有加碼回 null。 */
export function premiumLabel(rate: unknown): string | null {
  const percent = premiumPercent(rate);
  return percent === null ? null : `多拿 ${percent}% 購物金`;
}
