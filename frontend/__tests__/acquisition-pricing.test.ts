import { describe, expect, it } from "vitest";

import {
  creditPremiumPreview,
  marginPct,
  maxAcquisitionCost,
  payableTotal,
  splitValid,
  suggestedBulkUnitPrice,
  suggestedListedPrice,
} from "@/features/acquisition/pricing";

describe("maxAcquisitionCost", () => {
  const rule = { discountCeilingPct: 60, minMarginPct: 40, minPriceMultiple: 2.0 };
  it("takes the stricter of margin/ceiling and multiple", () => {
    // max(60,40)=60 → byMargin=1000×0.4=400；byMultiple=1000/2=500 → min=400
    expect(maxAcquisitionCost(1000, rule)).toBe(400);
  });
  it("multiple rescues low-price items", () => {
    // ceiling 30 → byMargin=300×0.7=210；multiple 3 → byMultiple=100 → min=100
    expect(
      maxAcquisitionCost(300, { discountCeilingPct: 30, minMarginPct: 20, minPriceMultiple: 3 }),
    ).toBe(100);
  });
  it("resale<=0 → null", () => {
    expect(maxAcquisitionCost(0, rule)).toBeNull();
  });
});

describe("marginPct", () => {
  it("computes integer percent", () => {
    expect(marginPct(1000, 600)).toBe(40);
  });
  it("listed<=0 → null", () => {
    expect(marginPct(0, 100)).toBeNull();
  });
});

describe("suggestedListedPrice", () => {
  it("cost ÷ (1 − margin/100)", () => {
    expect(suggestedListedPrice(550, 45)).toBe(1000);
    expect(suggestedListedPrice(800, 0)).toBe(800);
  });
  it("margin out of 0–99 → null", () => {
    expect(suggestedListedPrice(100, 100)).toBeNull();
    expect(suggestedListedPrice(100, -1)).toBeNull();
  });
});

describe("suggestedBulkUnitPrice", () => {
  it("per-piece cost ÷ (1 − margin/100)", () => {
    expect(suggestedBulkUnitPrice(300, 10, 50)).toBe(60); // 30 / 0.5
  });
  it("qty<=0 → null", () => {
    expect(suggestedBulkUnitPrice(300, 0, 50)).toBeNull();
  });
});

describe("payableTotal / splitValid / creditPremiumPreview", () => {
  it("payableTotal sums costs", () => {
    expect(payableTotal([100, 200, 300])).toBe(600);
    expect(payableTotal([])).toBe(0);
  });
  it("splitValid requires integer 0<cash<total", () => {
    expect(splitValid(1000, 400)).toBe(true);
    expect(splitValid(1000, 0)).toBe(false);
    expect(splitValid(1000, 1000)).toBe(false);
    expect(splitValid(1000, 400.5)).toBe(false);
  });
  it("creditPremiumPreview rounds", () => {
    expect(creditPremiumPreview(1000, 0.1)).toBe(100);
    expect(creditPremiumPreview(333, 0.1)).toBe(33); // 33.3 → 33
  });
});
