// /stocktake 純函式：實點數解析/驗證、差異計算、彙總、確認 payload、確認閘門、狀態徽章。
import { describe, expect, it } from "vitest";

import {
  buildConfirmCounts,
  canConfirm,
  type CountEntry,
  countError,
  parseCount,
  stStatusBadge,
  summarize,
  variance,
} from "@/features/stocktake/stocktake";

describe("parseCount", () => {
  it("treats blank as not-counted (null) and parses non-negative integers", () => {
    expect(parseCount("")).toBeNull();
    expect(parseCount("   ")).toBeNull();
    expect(parseCount("0")).toBe(0);
    expect(parseCount("12")).toBe(12);
  });

  it("returns null for invalid input", () => {
    expect(parseCount("-1")).toBeNull();
    expect(parseCount("1.5")).toBeNull();
    expect(parseCount("abc")).toBeNull();
  });
});

describe("countError", () => {
  it("allows blank (uncounted) but rejects malformed / negative", () => {
    expect(countError("")).toBeNull();
    expect(countError("3")).toBeNull();
    expect(countError("-2")).not.toBeNull();
    expect(countError("2.5")).not.toBeNull();
    expect(countError("x")).not.toBeNull();
  });
});

describe("variance", () => {
  it("is counted minus system, or null when uncounted", () => {
    expect(variance(10, 7)).toBe(-3);
    expect(variance(10, 12)).toBe(2);
    expect(variance(10, null)).toBeNull();
  });
});

function entry(systemQty: number, input: string): CountEntry {
  return { systemQty, input };
}

describe("summarize", () => {
  it("counts entered/uncounted lines and nets the variance", () => {
    const s = summarize([
      entry(10, "7"), // -3
      entry(5, "8"), //  +3
      entry(4, ""), //   uncounted
      entry(2, "2"), //  0
    ]);
    expect(s.counted).toBe(3);
    expect(s.uncounted).toBe(1);
    expect(s.short).toBe(3); // total short units
    expect(s.over).toBe(3); // total over units
    expect(s.net).toBe(0);
  });
});

describe("buildConfirmCounts", () => {
  it("includes only entered counts, mapped to product ids", () => {
    const counts = buildConfirmCounts([
      { catalog_product_id: 1, systemQty: 10, input: "7" },
      { catalog_product_id: 2, systemQty: 5, input: "" },
      { catalog_product_id: 3, systemQty: 2, input: "2" },
    ]);
    expect(counts).toEqual([
      { catalog_product_id: 1, counted_qty: 7 },
      { catalog_product_id: 3, counted_qty: 2 },
    ]);
  });
});

describe("canConfirm", () => {
  it("needs DRAFT, no errors and at least one entered count", () => {
    expect(canConfirm("CONFIRMED", [entry(10, "7")])).toBe(false);
    expect(canConfirm("DRAFT", [entry(10, "")])).toBe(false);
    expect(canConfirm("DRAFT", [entry(10, "-1")])).toBe(false);
    expect(canConfirm("DRAFT", [entry(10, "7")])).toBe(true);
  });
});

describe("stStatusBadge", () => {
  it("maps status to label + tone", () => {
    expect(stStatusBadge("DRAFT").label).toBe("盤點中");
    expect(stStatusBadge("CONFIRMED").tone).toBe("ok");
  });
});
