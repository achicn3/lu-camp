// 餐飲選項在購物車裡（docs/44 §3.6、O2）：同品項不同選項是不同行、選項排序後送出，
// 沒選項時送出形狀與以前完全相同（冪等指紋與客顯快照不受影響）。
import { describe, expect, it } from "vitest";

import { type CartLine, addLine, menuLineKey, refillLabel, refillUnitPrice, toSaleLines } from "@/features/pos/cart";
import { restoreLines } from "@/features/customer-display/PosCustomerDisplay";

function latte(options: number[], qty = 1): CartLine {
  return {
    key: menuLineKey(5, options),
    lineType: "MENU",
    description: "拿鐵（冰、燕麥奶）",
    unitPrice: 170,
    qty,
    menuItemId: 5,
    menuOptionIds: options,
  };
}

describe("餐飲選項 × 購物車", () => {
  it("同品項同選項合併數量；不同選項分開兩行；選項順序不影響", () => {
    let lines = addLine([], latte([7, 3])).lines;
    lines = addLine(lines, latte([3, 7])).lines;
    lines = addLine(lines, latte([2, 3])).lines;
    expect(lines.map((l) => [l.key, l.qty])).toEqual([
      ["MENU-5-3,7", 2],
      ["MENU-5-2,3", 1],
    ]);
  });

  it("沒選項的鍵維持舊形狀", () => {
    expect(menuLineKey(5, [])).toBe("MENU-5");
  });

  it("送出時選項排序後放 menu_option_ids；沒選項不放這個欄位", () => {
    const [withOptions, plain] = toSaleLines([
      latte([7, 3]),
      { key: "MENU-9", lineType: "MENU", description: "水", unitPrice: 10, qty: 1, menuItemId: 9 },
    ]);
    expect(withOptions.menu_option_ids).toEqual([3, 7]);
    expect("menu_option_ids" in plain).toBe(false);
  });

  it("從客顯快照還原：帶選項的餐飲行不會被丟掉，鍵與點磚時相同", () => {
    const restored = restoreLines([
      {
        item_key: "MENU:5:3,7",
        line_type: "MENU",
        name: "拿鐵（冰、燕麥奶）",
        qty: 2,
        unit_price: "170",
        line_kind: "NORMAL",
      } as never,
      {
        item_key: "MENU:9",
        line_type: "MENU",
        name: "水",
        qty: 1,
        unit_price: "10",
        line_kind: "NORMAL",
      } as never,
    ]);
    expect(restored.map((l) => [l.key, l.menuItemId, l.menuOptionIds ?? []])).toEqual([
      ["MENU-5-3,7", 5, [3, 7]],
      ["MENU-9", 9, []],
    ]);
  });
});

describe("續點（店主 2026-10-16）", () => {
  const refill = (options: number[]): CartLine => ({
    ...latte(options), key: menuLineKey(5, options, true), description: "續點・拿鐵", unitPrice: 136, menuRefill: true,
  });

  it("續點與一般點是不同行；沒續點的鍵不變", () => {
    expect(menuLineKey(5, [3], true)).toBe("MENU-5-3-refill");
    expect(menuLineKey(5, [], true)).toBe("MENU-5-refill");
    expect(menuLineKey(5, [3])).toBe("MENU-5-3");
    let lines = addLine([], latte([3])).lines;
    lines = addLine(lines, refill([3])).lines;
    expect(lines.map((l) => l.key)).toEqual(["MENU-5-3", "MENU-5-3-refill"]);
  });

  it("送出時續點帶 menu_refill；一般點不放這個欄位", () => {
    const [plain, cup] = toSaleLines([latte([3]), refill([3])]);
    expect("menu_refill" in plain).toBe(false);
    expect(cup.menu_refill).toBe(true);
  });

  it("從客顯快照還原：續點行保留續點，鍵與點磚時相同", () => {
    const [restored] = restoreLines([
      {
        item_key: "MENU:5:3:refill",
        line_type: "MENU",
        name: "續點・拿鐵（冰）",
        qty: 1,
        unit_price: "120",
        line_kind: "NORMAL",
      } as never,
    ]);
    expect(restored).toMatchObject({ key: "MENU-5-3-refill", menuItemId: 5, menuOptionIds: [3], menuRefill: true });
    const [bare] = restoreLines([
      { item_key: "MENU:5:refill", line_type: "MENU", name: "續點・水", qty: 1, unit_price: "8", line_kind: "NORMAL" } as never,
    ]);
    expect(bare).toMatchObject({ key: "MENU-5-refill", menuRefill: true });
    expect(bare?.menuOptionIds).toBeUndefined();
  });
});

describe("續點價（與後端 round_ntd 一致）", () => {
  it("照折數算、四捨五入到元；折數說法照台灣習慣", () => {
    expect(refillUnitPrice(170, 80)).toBe(136);
    expect(refillUnitPrice(10, 85)).toBe(9); // 8.5 → 9
    expect(refillUnitPrice(11, 85)).toBe(9); // 9.35 → 9
    expect(refillLabel(80)).toBe("8 折");
    expect(refillLabel(85)).toBe("85 折");
  });
});
