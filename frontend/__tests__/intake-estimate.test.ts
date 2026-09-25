import { describe, expect, it } from "vitest";

import { estimateLine, pctToDiscount, pricingRates } from "@/features/intake/estimate";

const RATES = pricingRates({
  default_margin_pct: 45,
  tax_rate: "0.0500",
  linepay_fee_pct: "0.0220",
  taiwanpay_fee_pct: "0.0100",
});

describe("收購佇列估價（與收購頁同一套計價）", () => {
  it("原價 1000 五折：預計賣 500、建議收購價與收購頁相同（256）、推斷 A", () => {
    expect(estimateLine("1000", "5", RATES)).toEqual({
      listed: 500,
      suggestedCost: 256,
      inferredGrade: "A",
      nearNew: false,
    });
  });

  it("六折以上標記可能是新品", () => {
    expect(estimateLine("1000", "6", RATES).nearNew).toBe(true);
    expect(estimateLine("1000", "5.9", RATES).nearNew).toBe(false);
  });

  it("沒原價或設定還沒讀到就不算建議價", () => {
    expect(estimateLine("", "5", RATES).suggestedCost).toBeNull();
    expect(estimateLine("1000", "5", pricingRates(null)).suggestedCost).toBeNull();
  });

  it("手續費取兩種支付較高者；十分位折數互轉", () => {
    expect(RATES.feeRate).toBe(0.022);
    expect([pctToDiscount(65), pctToDiscount(50), pctToDiscount(null)]).toEqual(["6.5", "5", ""]);
  });
});
