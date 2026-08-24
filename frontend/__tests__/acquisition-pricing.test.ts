import { describe, expect, it } from "vitest";

import {
  creditPremiumPreview,
  marginPct,
  maxAcquisitionCost,
  netOfTaxInclusive,
  payableTotal,
  splitValid,
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
  it("合法 Numeric(12,0) 大額仍以精確 ROUND_HALF_UP 換成含稅價", () => {
    // 精確值 901042174999 × 10001 / 10000 = 901132279216.4999，應捨為 901132279216。
    expect(taxInclusivePrice(901042174999, 0.0001)).toBe(901132279216);
  });
  it("未稅 <= 0 → null", () => {
    expect(taxInclusivePrice(0, RATE)).toBeNull();
    expect(taxInclusivePrice(-1, RATE)).toBeNull();
  });
});

describe("netOfTaxInclusive", () => {
  it("合法 Numeric(12,0) 大額仍以精確 ROUND_HALF_UP 還原未稅價", () => {
    // 精確值 835101086759 × 10000 / 10001 = 835017585000.4999…，應捨為 835017585000。
    expect(netOfTaxInclusive(835101086759, 0.0001)).toBe(835017585000);
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
  it("剛好半個百分點時精確四捨五入，不得被浮點誤差壓低", () => {
    // 含稅 42 → 未稅 40；(40 − 17) / 40 × 100 = 57.5%，應顯示 58%。
    expect(marginPct(42, 17, RATE)).toBe(58);
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
    // 未稅 = 600/0.55 = 1090.909…；單次取整 ×1.05 → 1145.4545… → 1145
    // 兩段式（先取整未稅 1091 再 ×1.05 = 1145.55）會得 1146 → 這條才分得出來
    expect(suggestedListedPrice(600, 45, RATE)).toBe(1145);
  });

  it("與後端 core/money.suggested_price 逐值一致（.5 邊界不得掉一元）", () => {
    // cost 87 / margin 10：未稅 96.666…×1.05 = 101.5 整。
    // 純浮點除法會算成 101.49999999999999 而少一元（後端 Decimal 得 102）。
    expect(suggestedListedPrice(87, 10, RATE)).toBe(102);
    // cost 41 / margin 18：精確值 41 × 105 ÷ 82 = 52.5 整 → 53。
    // 最自然的浮點寫法 `(cost/(1-m/100))*(1+rate)` 會算成 52.49999999999999 → 52
    // （該寫法在 cost 1–30000 × margin 0–99 中有 2.26% 的組合少一元）。
    expect(suggestedListedPrice(41, 18, RATE)).toBe(53);
    expect(suggestedListedPrice(1000, 45, RATE)).toBe(1909);
  });
  it("合法的 Numeric(12,0) 大額輸入仍與後端 Decimal 逐元一致", () => {
    // 精確值 284633213106 × 105 ÷ 60 = 498108122935.5 → ROUND_HALF_UP 498108122936。
    // 直接用 Number 做大分子乘法會超出安全整數，曾少算 1 元。
    expect(suggestedListedPrice(284633213106, 40, RATE)).toBe(498108122936);
  });
  it("稅率 0 → 等同舊行為", () => {
    expect(suggestedListedPrice(550, 45, 0)).toBe(1000);
  });
  it("margin out of 0–99 → null", () => {
    expect(suggestedListedPrice(100, 100, RATE)).toBeNull();
    expect(suggestedListedPrice(100, -1, RATE)).toBeNull();
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
