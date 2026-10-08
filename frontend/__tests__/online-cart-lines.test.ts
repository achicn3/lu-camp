import { describe, expect, it } from "vitest";

import { onlineCartLines } from "@/features/online-orders/onlineCartLines";
import type { OnlineCart } from "@/features/online-orders/OnlineOrdersPanel";
import { menuLineKey, removeLine, setQty, uniqueLineKeys } from "@/features/pos/cart";

type Line = OnlineCart["lines"][number];

function line(no: number, overrides: Partial<Line> = {}): Line {
  return {
    line_no: no,
    line_type: "MENU",
    menu_item_id: 7,
    catalog_product_id: null,
    menu_option_ids: [3, 1],
    experience_id: null,
    qty: 1,
    description: "手沖咖啡（蜜桃蹦蹦、冰）",
    online_unit_price: "290",
    unit_price: "290",
    ...overrides,
  };
}

describe("線上單帶入 POS 購物車（M1c；Codex 審查）", () => {
  it("一般點的行沿用點磚的鍵，之後再點同一杯會合併", () => {
    const [plain] = onlineCartLines([line(1)]);
    expect(plain?.key).toBe(menuLineKey(7, [1, 3]));
    expect(plain).toMatchObject({ lineType: "MENU", unitPrice: 290, qty: 1, menuItemId: 7, menuOptionIds: [3, 1] });
  });

  it("體驗卡與同品項同選項的一般點分成兩行：改數量、移除互不影響", () => {
    const lines = onlineCartLines([
      line(1, { experience_id: 4, description: "蜜桃蹦蹦體驗・手沖咖啡（蜜桃蹦蹦、冰）" }),
      line(2, { qty: 2 }),
    ]);
    expect(new Set(lines.map((l) => l.key)).size).toBe(2);
    expect(lines[0]?.description).toBe("蜜桃蹦蹦體驗・手沖咖啡（蜜桃蹦蹦、冰）");
    const [card, plain] = lines;
    const changed = setQty(lines, card!.key, 3);
    expect(changed.map((l) => l.qty)).toEqual([3, 2]);
    expect(removeLine(lines, plain!.key)).toEqual([card]);
  });

  it("萬一還有重複的鍵（同一張卡兩行、舊資料），後面的行另給鍵，不會互相蓋掉", () => {
    const lines = onlineCartLines([line(1), line(2), line(3, { experience_id: 4 }), line(4, { experience_id: 4 })]);
    expect(new Set(lines.map((l) => l.key)).size).toBe(4);
  });

  it("沒選項的行不帶 menuOptionIds（和點磚形狀一致）", () => {
    const [plain] = onlineCartLines([line(1, { menu_option_ids: [] })]);
    expect(plain).not.toHaveProperty("menuOptionIds");
    expect(plain?.key).toBe(menuLineKey(7, []));
  });
});

describe("POS 從伺服器還原購物車（Codex M1c 第二輪）", () => {
  it("還原後同鍵的兩行另給鍵，改數量／移除互不影響；沒重複的鍵不動", () => {
    const base = { lineType: "MENU" as const, description: "手沖咖啡（蜜桃蹦蹦、冰）", unitPrice: 290, qty: 1, menuItemId: 7 };
    const key = menuLineKey(7, [1, 3]);
    const lines = uniqueLineKeys([
      { ...base, key },
      { ...base, key, qty: 2 },
      { ...base, key: "C:5", lineType: "CATALOG" as const },
    ]);
    expect(lines.map((l) => l.key)).toEqual([key, `${key}#2`, "C:5"]);
    expect(setQty(lines, lines[1]!.key, 5).map((l) => l.qty)).toEqual([1, 5, 1]);
    expect(removeLine(lines, lines[0]!.key)).toHaveLength(2);
  });
});

describe("帶回家商品（docs/63 §13、M1d）", () => {
  it("帶入成一般商品行：鍵與掃碼加入的一致，數量照單", () => {
    const [bean] = onlineCartLines([
      line(1, {
        line_type: "CATALOG", menu_item_id: null, catalog_product_id: 41, menu_option_ids: [],
        description: "耶加雪菲 200g", unit_price: "450", qty: 2,
      }),
    ]);
    expect(bean).toEqual({
      key: "C:41", lineType: "CATALOG", description: "耶加雪菲 200g", unitPrice: 450, qty: 2, catalogProductId: 41,
    });
  });
});
