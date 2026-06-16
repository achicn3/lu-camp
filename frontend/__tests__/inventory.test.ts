import { describe, expect, it } from "vitest";

import {
  bulkStatusBadge,
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
    expect(gradeLabel("A")).toBe("A 級");
  });
});

describe("orUndefined", () => {
  it("maps empty string to undefined, keeps non-empty", () => {
    expect(orUndefined("")).toBeUndefined();
    expect(orUndefined("IN_STOCK")).toBe("IN_STOCK");
  });
});
