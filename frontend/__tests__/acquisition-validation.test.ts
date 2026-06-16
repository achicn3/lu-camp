import { describe, expect, it } from "vitest";

import {
  type AcquisitionDraft,
  type ItemDraft,
  type LotDraft,
  isPositiveIntNtd,
  lotErrors,
  payoutErrors,
  serializedRowErrors,
  validateDraft,
} from "@/features/acquisition/validation";

function item(over: Partial<ItemDraft> = {}): ItemDraft {
  return {
    name: "外套",
    grade: "A",
    categoryId: 1,
    brandId: null,
    productModelId: null,
    listedPrice: "3000",
    acquisitionCost: "1200",
    commissionPct: "50",
    ...over,
  };
}

function lot(over: Partial<LotDraft> = {}): LotDraft {
  return {
    name: "雜物堆",
    categoryId: null,
    brandId: null,
    acquisitionCost: "300",
    acquisitionBasis: "BAG",
    totalQty: "10",
    unitPrice: "50",
    label: "",
    ...over,
  };
}

describe("isPositiveIntNtd", () => {
  it("accepts positive integers only", () => {
    expect(isPositiveIntNtd("100")).toBe(true);
    expect(isPositiveIntNtd("0")).toBe(false);
    expect(isPositiveIntNtd("-5")).toBe(false);
    expect(isPositiveIntNtd("10.5")).toBe(false);
    expect(isPositiveIntNtd("abc")).toBe(false);
  });
});

describe("serializedRowErrors", () => {
  it("valid buyout row → no errors", () => {
    expect(serializedRowErrors("BUYOUT", 0, item())).toEqual([]);
  });
  it("buyout missing name/grade/category/cost", () => {
    const errs = serializedRowErrors(
      "BUYOUT",
      0,
      item({ name: " ", grade: "", categoryId: null, acquisitionCost: "0" }),
    );
    expect(errs.length).toBe(4);
  });
  it("consignment requires commission 0–100, not cost", () => {
    expect(serializedRowErrors("CONSIGNMENT", 0, item({ commissionPct: "50" }))).toEqual([]);
    expect(
      serializedRowErrors("CONSIGNMENT", 0, item({ commissionPct: "150" })),
    ).toContain("第 1 列：抽成需介於 0–100");
  });
});

describe("lotErrors", () => {
  it("valid lot → none", () => {
    expect(lotErrors(lot())).toEqual([]);
  });
  it("flags missing basis and bad qty", () => {
    const errs = lotErrors(lot({ acquisitionBasis: "", totalQty: "0" }));
    expect(errs.some((e) => e.includes("收購基準"))).toBe(true);
    expect(errs.some((e) => e.includes("件數"))).toBe(true);
  });
});

describe("payoutErrors", () => {
  it("store credit needs member", () => {
    expect(payoutErrors("STORE_CREDIT", false, 1000, "")).toHaveLength(1);
    expect(payoutErrors("STORE_CREDIT", true, 1000, "")).toEqual([]);
  });
  it("split cash must be 0<cash<total integer", () => {
    expect(payoutErrors("SPLIT", true, 1000, "400")).toEqual([]);
    expect(payoutErrors("SPLIT", true, 1000, "1000")).toHaveLength(1);
    expect(payoutErrors("CASH", false, 1000, "")).toEqual([]);
  });
});

describe("validateDraft", () => {
  const base: AcquisitionDraft = {
    type: "BUYOUT",
    contactId: 7,
    items: [item()],
    lot: lot(),
    payoutMethod: "CASH",
    payoutSplitCash: "",
    sellerIsMember: false,
  };
  it("valid buyout → no errors", () => {
    expect(validateDraft(base)).toEqual([]);
  });
  it("missing seller flagged", () => {
    expect(validateDraft({ ...base, contactId: null })).toContain("請先選擇或建立賣方/寄售人");
  });
  it("bulk lot path validates lot not items", () => {
    expect(validateDraft({ ...base, type: "BULK_LOT" })).toEqual([]);
  });
});
