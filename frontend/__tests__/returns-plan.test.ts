import { describe, expect, it } from "vitest";

import {
  computeRefund,
  isReturnable,
  remainingQty,
  validateReturnPlan,
} from "@/features/returns/plan";
import type { components } from "@/lib/api-types";

type SaleLine = components["schemas"]["SaleLineRead"];

function line(overrides: Partial<SaleLine>): SaleLine {
  return {
    id: 1,
    line_type: "CATALOG",
    description: "瓦斯罐",
    qty: 3,
    unit_price: "100",
    line_total: "300",
    discount_amount: "0",
    catalog_product_id: 1,
    serialized_item_id: null,
    bulk_lot_id: null,
    menu_item_id: null,
    ...overrides,
  } as SaleLine;
}

describe("returns plan", () => {
  it("餐飲不可退、三種實體品可退", () => {
    expect(isReturnable(line({ line_type: "MENU" }))).toBe(false);
    for (const t of ["CATALOG", "SERIALIZED", "BULK_LOT"] as const) {
      expect(isReturnable(line({ line_type: t }))).toBe(true);
    }
  });

  it("退款預估＝折後單價×數量（多行加總）", () => {
    const lines = [line({ id: 1, unit_price: "100" }), line({ id: 2, unit_price: "250", qty: 2 })];
    expect(computeRefund(lines, { 1: 2, 2: 1 })).toBe(450);
    expect(computeRefund(lines, {})).toBe(0);
  });

  it("可退餘量＝購買數−已退數", () => {
    expect(remainingQty(line({ qty: 3 }))).toBe(3);
    expect(remainingQty(line({ qty: 3, returned_qty: 2 }))).toBe(1);
    expect(remainingQty(line({ qty: 3, returned_qty: 3 }))).toBe(0);
  });

  it("防呆：原因必填、至少一項、不可超可退餘量、餐飲擋下", () => {
    const l = line({ id: 1 });
    expect(validateReturnPlan([l], { 1: 1 }, " ")).toContain("原因");
    expect(validateReturnPlan([l], {}, "壞了")).toContain("至少");
    expect(validateReturnPlan([l], { 1: 4 }, "壞了")).toContain("可退餘量");
    // 已退 2、購買 3 → 只能再退 1，退 2 被擋
    const partial = line({ id: 3, qty: 3, returned_qty: 2 });
    expect(validateReturnPlan([partial], { 3: 2 }, "壞了")).toContain("可退餘量 1");
    expect(validateReturnPlan([partial], { 3: 1 }, "壞了")).toBeNull();
    const menu = line({ id: 2, line_type: "MENU", description: "拿鐵" });
    expect(validateReturnPlan([menu], { 2: 1 }, "壞了")).toContain("餐飲");
    expect(validateReturnPlan([l], { 1: 2 }, "壞了")).toBeNull();
  });
});
