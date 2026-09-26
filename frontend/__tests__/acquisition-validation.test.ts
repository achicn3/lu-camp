import { describe, expect, it } from "vitest";

import {
  type AcquisitionDraft,
  type ItemDraft,
  type LotDraft,
  isPositiveIntNtd,
  lotErrors,
  payoutErrors,
  serializedRowErrors,
  validateCombined,
  validateDraft,
} from "@/features/acquisition/validation";

function item(over: Partial<ItemDraft> = {}): ItemDraft {
  return {
    name: "外套",
    grade: "A",
    categoryId: 1,
    brandId: null,
    productModelId: null,
    note: "",
    listedPrice: "3000",
    retailPrice: "",
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
    retailPrice: "",
    label: "",
    note: "",
    basketMode: "NONE",
    basketId: null,
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
  it("折數模式必須有有效折數與正數參考價，即使手動填完售價也不能跳過", () => {
    expect(serializedRowErrors("BUYOUT", 0, item({ discount: "11", retailPrice: "1000" })))
      .toContain("第 1 列：折數須為 0.1–10，最多一位小數");
    expect(serializedRowErrors("BUYOUT", 0, item({ discount: "5", retailPrice: "" })))
      .toContain("第 1 列：請輸入正整數參考價");
  });
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
  it("全新未拆（N）是合法成色，買斷與寄售都能選", () => {
    expect(serializedRowErrors("BUYOUT", 0, item({ grade: "N" }))).toEqual([]);
    expect(
      serializedRowErrors("CONSIGNMENT", 0, item({ grade: "N", commissionPct: "50" })),
    ).toEqual([]);
  });
  it("散裝的 E 不能出現在序號品；沒選成色時提示要列出全新未拆", () => {
    expect(serializedRowErrors("BUYOUT", 0, item({ grade: "E" }))).toContain(
      "第 1 列：成色必選（全新未拆、S–D）",
    );
    expect(serializedRowErrors("BUYOUT", 0, item({ grade: "" }))).toContain(
      "第 1 列：成色必選（全新未拆、S–D）",
    );
  });
  it("consignment requires commission 0–100, not cost", () => {
    expect(serializedRowErrors("CONSIGNMENT", 0, item({ commissionPct: "50" }))).toEqual([]);
    expect(
      serializedRowErrors("CONSIGNMENT", 0, item({ commissionPct: "150" })),
    ).toContain("第 1 列：抽成需介於 0–100");
  });
});

describe("lotErrors", () => {
  it("加入現有販售籃必須選定是哪一籃", () => {
    expect(lotErrors(lot({ basketMode: "JOIN" }))).toContain("散裝：請選擇要加入的販售籃");
    expect(lotErrors(lot({ basketMode: "JOIN", basketId: 5 }))).toEqual([]);
  });
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

describe("validateCombined（收購①：買斷再加散裝）", () => {
  const buyout: AcquisitionDraft = {
    type: "BUYOUT",
    contactId: 1,
    items: [item()],
    lot: lot(),
    payoutMethod: "CASH",
    payoutSplitCash: "",
    sellerIsMember: false,
  };

  it("買斷與每堆散裝都驗，散裝錯誤標出第幾堆", () => {
    expect(validateCombined(buyout, [lot(), lot({ name: "" })])).toEqual(["第 2 堆散裝：名稱必填"]);
  });

  it("不提供混合撥款", () => {
    expect(validateCombined({ ...buyout, payoutMethod: "SPLIT", payoutSplitCash: "100" }, [lot()])).toContain(
      "買斷加散裝一起收時，撥款只能全付現金或全給購物金",
    );
  });

  it("都填好就沒有錯誤", () => {
    expect(validateCombined(buyout, [lot()])).toEqual([]);
  });
});
