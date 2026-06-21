// Unit tests for pure helpers in features/campaigns/campaigns.ts
import { describe, expect, it } from "vitest";

import {
  bearingLabel,
  discountDisplay,
  scopeSummary,
  statusLabel,
} from "@/features/campaigns/campaigns";

describe("discountDisplay", () => {
  it("converts discount_pct=10 to 9 折", () => {
    expect(discountDisplay(10)).toBe("9 折");
  });

  it("converts discount_pct=50 to 5 折", () => {
    expect(discountDisplay(50)).toBe("5 折");
  });

  it("converts discount_pct=5 to 95 折 (i.e. 5% off)", () => {
    expect(discountDisplay(5)).toBe("95 折");
  });

  it("converts discount_pct=99 to 1 折", () => {
    expect(discountDisplay(99)).toBe("1 折");
  });

  it("converts discount_pct=1 to 99 折", () => {
    expect(discountDisplay(1)).toBe("99 折");
  });

  it("converts discount_pct=15 to 85 折", () => {
    expect(discountDisplay(15)).toBe("85 折");
  });

  it("converts discount_pct=25 to 75 折", () => {
    expect(discountDisplay(25)).toBe("75 折");
  });
});

describe("statusLabel", () => {
  it("returns correct zh-TW labels", () => {
    expect(statusLabel("DRAFT")).toBe("草稿");
    expect(statusLabel("ACTIVE")).toBe("生效中");
    expect(statusLabel("ENDED")).toBe("已結束");
    expect(statusLabel("CANCELLED")).toBe("已作廢");
  });
});

describe("scopeSummary", () => {
  it("shows all enabled categories", () => {
    const result = scopeSummary({
      applies_owned_serialized: true,
      applies_owned_bulk: true,
      applies_catalog: true,
      applies_consignment: true,
    });
    expect(result).toContain("自有序號");
    expect(result).toContain("自有散裝");
    expect(result).toContain("數量型商品");
    expect(result).toContain("寄售");
  });

  it("shows only enabled categories", () => {
    const result = scopeSummary({
      applies_owned_serialized: true,
      applies_owned_bulk: false,
      applies_catalog: false,
      applies_consignment: false,
    });
    expect(result).toBe("自有序號");
  });

  it("returns dash for none enabled", () => {
    const result = scopeSummary({
      applies_owned_serialized: false,
      applies_owned_bulk: false,
      applies_catalog: false,
      applies_consignment: false,
    });
    expect(result).toBe("-");
  });
});

describe("bearingLabel", () => {
  it("returns correct zh-TW labels", () => {
    expect(bearingLabel("STORE_ABSORBS")).toBe("店家吸收");
    expect(bearingLabel("PROPORTIONAL")).toBe("比例分攤");
  });
});
