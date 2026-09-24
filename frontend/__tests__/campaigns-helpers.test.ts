// Unit tests for pure helpers in features/campaigns/campaigns.ts
import { describe, expect, it } from "vitest";

import {
  discountDisplay,
  scopeSummary,
  statusLabel, offerDisplay } from "@/features/campaigns/campaigns";

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
    expect(result).toContain("一般商品");
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

describe("offerDisplay（docs/40 P2）", () => {
  it("打折、指定特價、每件折金額各有說法", () => {
    expect(offerDisplay({ kind: "PERCENT_OFF", discount_pct: 10, fixed_price: null, amount_off: null })).toBe("9 折");
    expect(offerDisplay({ kind: "FIXED_PRICE", discount_pct: null, fixed_price: "690", amount_off: null })).toBe("特價 $690");
    expect(offerDisplay({ kind: "AMOUNT_OFF", discount_pct: null, fixed_price: null, amount_off: "100" })).toBe("每件折 $100");
  });
});

describe("offerDisplay（docs/40 P3）", () => {
  it("買幾送幾", () => {
    expect(offerDisplay({ kind: "BUY_N_GET_M", buy_qty: 5, free_qty: 1 })).toBe("買 5 送 1");
  });
});
