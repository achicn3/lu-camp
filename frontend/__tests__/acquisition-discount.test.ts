import { describe, expect, it } from "vitest";

import { discountedPrice, acquisitionFromListedPrice, gradeFromDiscount } from "@/features/acquisition/pricing";

describe("依參考價與折數鑑價", () => {
  it("1000 元五折是客人實付 500，扣稅與手續費後按目標毛利反推收購價", () => {
    expect(discountedPrice(1000, "5")).toBe(500);
    // 未稅 476，支付費 11，實得 465；目標毛利 45% → 收購 256。
    expect(acquisitionFromListedPrice(500, 45, 0.05, 0.022)).toBe(256);
  });
  it("自訂折數與成色邊界", () => {
    expect(discountedPrice(1000, "6.5")).toBe(650);
    expect(gradeFromDiscount("7")).toBe("S");
    expect(gradeFromDiscount("6.9")).toBe("A");
    expect(gradeFromDiscount("5")).toBe("A");
    expect(gradeFromDiscount("3")).toBe("B");
    expect(gradeFromDiscount("2")).toBe("C");
  });
  it("無效金額、折數與毛利不能產生報價", () => {
    expect(discountedPrice(1000, "")).toBeNull();
    expect(discountedPrice(1000, "0")).toBeNull();
    expect(discountedPrice(1000, "11")).toBeNull();
    expect(acquisitionFromListedPrice(500, 100, 0.05, 0)).toBeNull();
    expect(acquisitionFromListedPrice(500, 45, 0.05, 1)).toBeNull();
  });
});
