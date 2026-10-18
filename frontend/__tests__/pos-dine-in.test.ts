import { describe, expect, it } from "vitest";

import {
  type DineInSelection,
  clearDineIn,
  dineInRequestFields,
  refillCheck,
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

  it("桌號清單是空的時，一開始就要說去哪裡設定——內用鍵是停用的，店員按不下去也問不到原因", () => {
    const v = validateDineIn(true, sel(), []);
    expect(v.ok).toBe(false);
    expect(v.tablesUnavailable).toBe(true);
    expect(v.error).toContain("設定");
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

// 店主 2026-10-19：外帶不能續點（擋下）；內用選好桌號就顯示本桌 90 分鐘內有沒有點過——
// 有就確認、沒有只提醒，照樣能結。
describe("續點檢查", () => {
  const latte = { menuItemId: 5, description: "續點・拿鐵（燕麥奶）" };
  const cake = { menuItemId: 7, description: "續點・蛋糕" };

  it("沒有續點：什麼都不說", () => {
    expect(refillCheck([], { mode: "TAKEOUT", tableNo: null }, null)).toEqual({ error: null, warning: null, found: null });
  });

  it("外帶有續點：擋下", () => {
    expect(refillCheck([latte], { mode: "TAKEOUT", tableNo: null }, null).error).toBe(
      "外帶不能續點，請把續點改成一般點，或改選內用",
    );
  });

  it("內用：點過的確認、沒點過的列出來提醒（都不擋）", () => {
    const table = { mode: "DINE_IN" as const, tableNo: "A1" };
    expect(refillCheck([latte], table, [5])).toEqual({
      error: null,
      warning: null,
      found: "本桌 90 分鐘內點過：續點・拿鐵（燕麥奶）",
    });
    expect(refillCheck([latte, cake], table, [5])).toEqual({
      error: null,
      warning: "本桌 90 分鐘內沒點過：續點・蛋糕。確定是續點再結帳。",
      found: "本桌 90 分鐘內點過：續點・拿鐵（燕麥奶）",
    });
    expect(refillCheck([cake], table, [5]).found).toBeNull();
  });

  it("還沒選桌號、或還沒查到：先不提醒", () => {
    const none = { error: null, warning: null, found: null };
    expect(refillCheck([latte], { mode: "DINE_IN", tableNo: null }, null)).toEqual(none);
    expect(refillCheck([latte], { mode: "DINE_IN", tableNo: "A1" }, null)).toEqual(none);
  });
});
