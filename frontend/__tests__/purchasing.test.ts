// /purchasing 純函式：採購單明細小計/總額、欄位驗證、狀態徽章、收貨閘門。
import { describe, expect, it } from "vitest";

import {
  canCancel,
  canEdit,
  canReceive,
  canSubmit,
  canSubmitPo,
  type DraftLine,
  draftTotal,
  hasReceipts,
  lineRemaining,
  lineTotal,
  poStatusBadge,
  qtyError,
  receivedQtyError,
  supplierNameError,
  toLinePayload,
  toUpdatePayload,
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
    is_active: true,
    product_model_id: null,
    category_id: null,
    unit_price: "100",
    quantity_on_hand: 5,
    reorder_point: 3,
    incoming_qty: 0,
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
  it("ORDERED and PARTIAL can be (further) received", () => {
    expect(canReceive("ORDERED")).toBe(true);
    expect(canReceive("PARTIAL")).toBe(true);
    expect(canReceive("DRAFT")).toBe(false);
    expect(canReceive("RECEIVED")).toBe(false);
    expect(canReceive("CANCELLED")).toBe(false);
  });
});

describe("canSubmit", () => {
  it("only DRAFT can be submitted", () => {
    expect(canSubmit("DRAFT")).toBe(true);
    expect(canSubmit("ORDERED")).toBe(false);
    expect(canSubmit("PARTIAL")).toBe(false);
  });
});

describe("canCancel", () => {
  it("only DRAFT / ORDERED (no goods received yet) can be cancelled", () => {
    expect(canCancel("DRAFT")).toBe(true);
    expect(canCancel("ORDERED")).toBe(true);
    expect(canCancel("PARTIAL")).toBe(false);
    expect(canCancel("RECEIVED")).toBe(false);
    expect(canCancel("CANCELLED")).toBe(false);
  });
});

describe("lineRemaining", () => {
  it("is ordered minus received, clamped at 0", () => {
    expect(lineRemaining(20, 12)).toBe(8);
    expect(lineRemaining(10, 10)).toBe(0);
    expect(lineRemaining(5, 8)).toBe(0);
  });
});

describe("poStatusBadge", () => {
  it("maps every status to a label + tone", () => {
    expect(poStatusBadge("ORDERED").label).toBe("已下單");
    expect(poStatusBadge("PARTIAL").label).toBe("部分到貨");
    expect(poStatusBadge("CANCELLED").label).toBe("已取消");
    expect(poStatusBadge("RECEIVED").tone).toBe("ok");
  });
});

describe("canEdit（docs/70 §2）", () => {
  it("草稿大家能改；下單後只有管理者；取消的誰都不能改", () => {
    expect(canEdit("DRAFT", false)).toBe(true);
    for (const status of ["ORDERED", "PARTIAL", "RECEIVED"] as const) {
      expect(canEdit(status, false)).toBe(false);
      expect(canEdit(status, true)).toBe(true);
    }
    expect(canEdit("CANCELLED", true)).toBe(false);
  });

  it("收過貨的單才有「已收」欄", () => {
    expect(hasReceipts("PARTIAL")).toBe(true);
    expect(hasReceipts("RECEIVED")).toBe(true);
    expect(hasReceipts("ORDERED")).toBe(false);
    expect(hasReceipts("DRAFT")).toBe(false);
  });
});

describe("receivedQtyError", () => {
  it("沒有已收欄就不檢查；有就要 0 到訂購數量之間的整數", () => {
    expect(receivedQtyError(line())).toBeNull();
    expect(receivedQtyError(line({ qty: 3, receivedQty: 3 }))).toBeNull();
    expect(receivedQtyError(line({ qty: 3, receivedQty: 0 }))).toBeNull();
    expect(receivedQtyError(line({ qty: 3, receivedQty: 4 }))).toBe("已收不可超過訂購數量");
    expect(receivedQtyError(line({ receivedQty: -1 }))).not.toBeNull();
    expect(receivedQtyError(line({ receivedQty: Number.NaN }))).not.toBeNull();
  });

  it("已收不合法就不能送出", () => {
    expect(canSubmitPo(1, [line({ qty: 2, receivedQty: 5 })])).toBe(false);
  });
});

describe("toUpdatePayload", () => {
  it("帶原列 id 與已收數量；新加的列 id 為 null、已收 0", () => {
    expect(
      toUpdatePayload([
        line({ lineId: 9, receivedQty: 1, unitCost: " 30 " }),
        line({ key: "k2", product: product(2), qty: 1, unitCost: "5" }),
      ]),
    ).toEqual([
      { id: 9, catalog_product_id: 1, qty: 2, received_qty: 1, unit_cost: "30" },
      { id: null, catalog_product_id: 2, qty: 1, received_qty: 0, unit_cost: "5" },
    ]);
  });
});
