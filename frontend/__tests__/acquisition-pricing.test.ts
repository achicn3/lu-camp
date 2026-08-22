import { describe, expect, it } from "vitest";

import {
  creditPremiumPreview,
  marginPct,
  maxAcquisitionCost,
  payableTotal,
  splitValid,
  suggestedBulkUnitPrice,
  suggestedListedPrice,
  taxInclusivePrice,
} from "@/features/acquisition/pricing";

const RATE = 0.05;

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

describe("taxInclusivePrice", () => {
  it("未稅 → 含稅（ROUND_HALF_UP，鏡射後端 round_ntd）", () => {
    // 店主的例子：估計轉售價 2010（未稅）→ 上架售價 2111（含稅）
    expect(taxInclusivePrice(2010, RATE)).toBe(2111); // 2110.5 → 2111
    expect(taxInclusivePrice(1000, RATE)).toBe(1050);
    expect(taxInclusivePrice(333, RATE)).toBe(350); // 349.65 → 350
  });
  it("稅率 0 → 原價", () => {
    expect(taxInclusivePrice(2010, 0)).toBe(2010);
  });
  it("非 5% 的稅率也要能算（稅率放 settings，不得寫死）", () => {
    expect(taxInclusivePrice(1000, 0.1)).toBe(1100);
  });
  it("未稅 <= 0 → null", () => {
    expect(taxInclusivePrice(0, RATE)).toBeNull();
    expect(taxInclusivePrice(-1, RATE)).toBeNull();
  });
});

describe("marginPct", () => {
  it("以**未稅**售價為分母（那 5% 是代收代付，不是毛利）", () => {
    // 含稅 2111 → 未稅 2010；(2010−1000)/2010 = 50%
    expect(marginPct(2111, 1000, RATE)).toBe(50);
    // 含稅 2010 → 未稅 1914；(1914−1000)/1914 = 48%（舊寫法會顯示 50%，高估）
    expect(marginPct(2010, 1000, RATE)).toBe(48);
  });
  it("稅率 0 時等同含稅＝未稅", () => {
    expect(marginPct(1000, 600, 0)).toBe(40);
  });
  it("listed<=0 → null", () => {
    expect(marginPct(0, 100, RATE)).toBeNull();
  });
});

describe("suggestedListedPrice", () => {
  it("回**含稅**價：cost ÷ (1 − margin/100) × (1 + 稅率)", () => {
    // 未稅 1000（550/0.55）→ 含稅 1050
    expect(suggestedListedPrice(550, 45, RATE)).toBe(1050);
    expect(suggestedListedPrice(800, 0, RATE)).toBe(840);
  });
  it("只四捨五入一次（不先取整未稅再取整含稅）", () => {
    // 未稅 = 100/0.55 = 181.8181…；×1.05 = 190.909… → 191
    // 若先取整未稅（182）再 ×1.05 = 191.1 → 191（此例同值，但規則以單次取整為準）
    expect(suggestedListedPrice(100, 45, RATE)).toBe(191);
  });
  it("稅率 0 → 等同舊行為", () => {
    expect(suggestedListedPrice(550, 45, 0)).toBe(1000);
  });
  it("margin out of 0–99 → null", () => {
    expect(suggestedListedPrice(100, 100, RATE)).toBeNull();
    expect(suggestedListedPrice(100, -1, RATE)).toBeNull();
  });
});

describe("suggestedBulkUnitPrice", () => {
  it("回**含稅**每件價：每件成本 ÷ (1 − margin/100) × (1 + 稅率)", () => {
    // 每件成本 30 → 未稅 60 → 含稅 63
    expect(suggestedBulkUnitPrice(300, 10, 50, RATE)).toBe(63);
  });
  it("稅率 0 → 等同舊行為", () => {
    expect(suggestedBulkUnitPrice(300, 10, 50, 0)).toBe(60);
  });
  it("qty<=0 → null", () => {
    expect(suggestedBulkUnitPrice(300, 0, 50, RATE)).toBeNull();
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
