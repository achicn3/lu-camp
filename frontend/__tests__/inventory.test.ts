import { describe, expect, it } from "vitest";

import {
  QUANTITY_ADJUST_REASONS,
  bulkStatusBadge,
  checkQuantityAdjust,
  gradeLabel,
  isLowStock,
  orUndefined,
  ownershipBadge,
  sellThroughPct,
  serializedStatusBadge,
} from "@/features/inventory/inventory";

describe("isLowStock", () => {
  it("low when at or below reorder point", () => {
    expect(isLowStock(3, 5)).toBe(true);
    expect(isLowStock(5, 5)).toBe(true); // 邊界：等於即低庫存
  });
  it("not low when above reorder point", () => {
    expect(isLowStock(6, 5)).toBe(false);
  });
});

describe("sellThroughPct", () => {
  it("computes sold ratio as integer percent", () => {
    expect(sellThroughPct(10, 3)).toBe(70);
    expect(sellThroughPct(10, 0)).toBe(100);
    expect(sellThroughPct(10, 10)).toBe(0);
  });
  it("guards total<=0 and clamps remaining out of range", () => {
    expect(sellThroughPct(0, 0)).toBe(0);
    expect(sellThroughPct(10, 99)).toBe(0); // remaining>total → sold 夾為 0
  });
});

describe("badge maps", () => {
  it("serialized status", () => {
    expect(serializedStatusBadge("IN_STOCK")).toEqual({ label: "在庫", tone: "ok" });
    expect(serializedStatusBadge("WRITTEN_OFF").tone).toBe("warn");
  });
  it("bulk status", () => {
    expect(bulkStatusBadge("ON_SALE").label).toBe("販售中");
    expect(bulkStatusBadge("SOLD_OUT").tone).toBe("muted");
  });
  it("ownership + grade", () => {
    expect(ownershipBadge("CONSIGNMENT").label).toBe("寄售");
    expect(ownershipBadge("OWNED").label).toBe("自有");
    expect(gradeLabel("S")).toBe("S 超熱門搶手貨");
    expect(gradeLabel("A")).toBe("A 近全新/精品");
    expect(gradeLabel("B")).toBe("B 良好");
    expect(gradeLabel("C")).toBe("C 普通");
    expect(gradeLabel("D")).toBe("D 較差");
    expect(gradeLabel("E")).toBe("E 散裝");
  });
});

describe("orUndefined", () => {
  it("maps empty string to undefined, keeps non-empty", () => {
    expect(orUndefined("")).toBeUndefined();
    expect(orUndefined("IN_STOCK")).toBe("IN_STOCK");
  });
});

describe("checkQuantityAdjust（一般商品手動改數量）", () => {
  it("減少：任何原因都可以", () => {
    expect(checkQuantityAdjust(5, "3", "DAMAGED")).toEqual({ newQty: 3 });
    expect(checkQuantityAdjust(5, " 0 ", "SELF_USE")).toEqual({ newQty: 0 });
  });
  it("增加：只有數量更正／其他可以", () => {
    expect(checkQuantityAdjust(5, "8", "COUNT_CORRECTION")).toEqual({ newQty: 8 });
    expect(checkQuantityAdjust(5, "8", "OTHER_ADJUST")).toEqual({ newQty: 8 });
    expect(checkQuantityAdjust(5, "8", "EXPIRED")).toHaveProperty("error");
  });
  it("數量沒變、非整數、負數都擋下", () => {
    expect(checkQuantityAdjust(5, "5", "OTHER_ADJUST")).toHaveProperty("error");
    expect(checkQuantityAdjust(5, "2.5", "OTHER_ADJUST")).toHaveProperty("error");
    expect(checkQuantityAdjust(5, "-1", "OTHER_ADJUST")).toHaveProperty("error");
    expect(checkQuantityAdjust(5, "", "OTHER_ADJUST")).toHaveProperty("error");
    expect(checkQuantityAdjust(5, "1000001", "OTHER_ADJUST")).toHaveProperty("error");
    expect(checkQuantityAdjust(5, "1000000", "OTHER_ADJUST")).toEqual({ newQty: 1000000 });
  });
  it("每個原因都有中文名稱", () => {
    expect(QUANTITY_ADJUST_REASONS.DAMAGED.label).toBe("破損");
    expect(QUANTITY_ADJUST_REASONS.SELF_USE.decreaseOnly).toBe(true);
    expect(QUANTITY_ADJUST_REASONS.COUNT_CORRECTION.decreaseOnly).toBe(false);
  });
});
