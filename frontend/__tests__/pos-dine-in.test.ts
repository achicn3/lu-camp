import { describe, expect, it } from "vitest";

import {
  type DineInSelection,
  clearDineIn,
  dineInRequestFields,
  validateDineIn,
} from "@/features/pos/dineIn";

const TABLES = ["A1", "A2", "B1"];

const sel = (over: Partial<DineInSelection> = {}): DineInSelection => ({
  mode: null,
  tableNo: null,
  ...over,
});

describe("內用/外帶與桌號（docs/35）", () => {
  it("購物車沒有餐飲行時不需要選，也不送任何欄位", () => {
    const v = validateDineIn(false, sel(), TABLES);
    expect(v.required).toBe(false);
    expect(v.ok).toBe(true);
    expect(dineInRequestFields(false, sel())).toEqual({});
  });

  it("有餐飲行但沒選內用/外帶 → 擋下並說明原因", () => {
    const v = validateDineIn(true, sel(), TABLES);
    expect(v.required).toBe(true);
    expect(v.ok).toBe(false);
    expect(v.error).toContain("內用或外帶");
  });

  it("外帶不需要桌號", () => {
    const v = validateDineIn(true, sel({ mode: "TAKEOUT" }), TABLES);
    expect(v.ok).toBe(true);
    expect(dineInRequestFields(true, sel({ mode: "TAKEOUT" }))).toEqual({
      service_mode: "TAKEOUT",
    });
  });

  it("內用未選桌號 → 擋下", () => {
    const v = validateDineIn(true, sel({ mode: "DINE_IN" }), TABLES);
    expect(v.ok).toBe(false);
    expect(v.error).toContain("桌號");
  });

  it("內用選了合法桌號 → 通過並帶出桌號", () => {
    const selection = sel({ mode: "DINE_IN", tableNo: "A2" });
    expect(validateDineIn(true, selection, TABLES).ok).toBe(true);
    expect(dineInRequestFields(true, selection)).toEqual({
      service_mode: "DINE_IN",
      table_no: "A2",
    });
  });

  it("桌號清單還沒維護 → 內用一律擋（fail closed，不讓自由打字繞過）", () => {
    const v = validateDineIn(true, sel({ mode: "DINE_IN", tableNo: "A1" }), []);
    expect(v.ok).toBe(false);
    expect(v.error).toContain("設定");
    expect(v.tablesUnavailable).toBe(true);
  });

  it("桌號被管理者從清單移除後仍留在畫面上 → 擋下，不讓它送到後端才 422", () => {
    const v = validateDineIn(true, sel({ mode: "DINE_IN", tableNo: "Z9" }), TABLES);
    expect(v.ok).toBe(false);
  });

  it("移除最後一筆餐飲後清空選擇——否則純二手的單會帶著桌號送出（後端 422）", () => {
    expect(clearDineIn()).toEqual({ mode: null, tableNo: null });
  });
});
