// /purchasing 純函式：採購單明細小計/總額、欄位驗證、狀態徽章、收貨閘門。
import { describe, expect, it } from "vitest";

import {
  canReceive,
  canSubmitPo,
  type DraftLine,
  draftTotal,
  lineTotal,
  poStatusBadge,
  qtyError,
  supplierNameError,
  toLinePayload,
  unitCostError,
} from "@/features/purchasing/purchasing";
import type { components } from "@/lib/api-types";

type CatalogProduct = components["schemas"]["CatalogProductRead"];

function product(id: number): CatalogProduct {
  return {
    id,
    store_id: 1,
    sku: `SKU-${id}`,
    name: `商品${id}`,
    brand_id: null,
    unit_price: "100",
    quantity_on_hand: 5,
    reorder_point: 3,
  };
}

function line(overrides: Partial<DraftLine> = {}): DraftLine {
  return { key: "k1", product: product(1), qty: 2, unitCost: "30", ...overrides };
}

describe("unitCostError", () => {
  it("rejects empty / non-integer / non-positive", () => {
    expect(unitCostError("")).not.toBeNull();
    expect(unitCostError("  ")).not.toBeNull();
    expect(unitCostError("12.5")).not.toBeNull();
    expect(unitCostError("abc")).not.toBeNull();
    expect(unitCostError("0")).not.toBeNull();
    expect(unitCostError("-5")).not.toBeNull();
  });

  it("accepts positive whole NTD", () => {
    expect(unitCostError("30")).toBeNull();
    expect(unitCostError("1,200")).toBeNull();
  });
});

describe("qtyError", () => {
  it("requires positive integer", () => {
    expect(qtyError(0)).not.toBeNull();
    expect(qtyError(-1)).not.toBeNull();
    expect(qtyError(1.5)).not.toBeNull();
    expect(qtyError(3)).toBeNull();
  });
});

describe("lineTotal", () => {
  it("multiplies qty by unit cost", () => {
    expect(lineTotal(line({ qty: 4, unitCost: "25" }))).toBe(100);
  });

  it("returns null when the line is invalid", () => {
    expect(lineTotal(line({ qty: 0 }))).toBeNull();
    expect(lineTotal(line({ unitCost: "x" }))).toBeNull();
  });
});

describe("draftTotal", () => {
  it("sums valid lines and ignores invalid ones", () => {
    const lines = [
      line({ key: "a", qty: 2, unitCost: "10" }),
      line({ key: "b", qty: 3, unitCost: "20" }),
      line({ key: "c", qty: 0, unitCost: "99" }),
    ];
    expect(draftTotal(lines)).toBe(2 * 10 + 3 * 20);
  });
});

describe("canSubmitPo", () => {
  it("needs a supplier and at least one valid line", () => {
    expect(canSubmitPo(null, [line()])).toBe(false);
    expect(canSubmitPo(1, [])).toBe(false);
    expect(canSubmitPo(1, [line({ unitCost: "" })])).toBe(false);
    expect(canSubmitPo(1, [line()])).toBe(true);
  });
});

describe("toLinePayload", () => {
  it("maps draft lines to the create payload shape", () => {
    const payload = toLinePayload([line({ qty: 2, unitCost: "1,200" })]);
    expect(payload).toEqual([{ catalog_product_id: 1, qty: 2, unit_cost: "1200" }]);
  });
});

describe("supplierNameError", () => {
  it("requires a non-blank name", () => {
    expect(supplierNameError("")).not.toBeNull();
    expect(supplierNameError("   ")).not.toBeNull();
    expect(supplierNameError("好供應商")).toBeNull();
  });
});

describe("canReceive", () => {
  it("only ORDERED can be received", () => {
    expect(canReceive("ORDERED")).toBe(true);
    expect(canReceive("DRAFT")).toBe(false);
    expect(canReceive("RECEIVED")).toBe(false);
    expect(canReceive("CLOSED")).toBe(false);
  });
});

describe("poStatusBadge", () => {
  it("maps every status to a label + tone", () => {
    expect(poStatusBadge("ORDERED").label).toBe("已下單");
    expect(poStatusBadge("RECEIVED").tone).toBe("ok");
  });
});
