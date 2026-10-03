// 購物金加碼字樣：寫「多拿幾 %」，不寫多得多少錢（店主 2026-10-03）。
import { describe, expect, it } from "vitest";

import { premiumLabel, premiumPercent } from "@/lib/storeCreditPremium";

describe("購物金加碼字樣", () => {
  it("比率轉成百分比，最多一位小數", () => {
    expect(premiumPercent("0.03")).toBe("3");
    expect(premiumPercent("0.025")).toBe("2.5");
    expect(premiumPercent(0.1)).toBe("10");
  });

  it("沒有加碼或讀不懂就不顯示", () => {
    expect(premiumPercent("0")).toBeNull();
    expect(premiumPercent(undefined)).toBeNull();
    expect(premiumPercent("abc")).toBeNull();
    expect(premiumLabel("0")).toBeNull();
  });

  it("字樣", () => {
    expect(premiumLabel("0.03")).toBe("多拿 3% 購物金");
  });
});
