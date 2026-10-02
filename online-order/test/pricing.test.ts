// 送單驗價（docs/44 §3.2、§8.1 T4、§8.2）：價格只在伺服器端依目前菜單算；不合法的品項／選項／數量一律拒收。
import { describe, expect, it } from "vitest";

import type { MenuSnapshot } from "../src/client/types";
import { priceOrder } from "../src/pricing";

const MENU: MenuSnapshot = {
  version: 7,
  published_at: "2026-10-02T03:00:00Z",
  store_name: "露坑",
  font: null,
  categories: [{ id: 1, name: "咖啡" }],
  items: [
    {
      id: 5,
      name: "拿鐵",
      description: null,
      category_id: 1,
      unit_price: 150,
      photo: null,
      available: true,
      remaining: null,
      option_groups: [
        {
          id: 1,
          name: "溫度",
          min_select: 1,
          max_select: 1,
          options: [
            { id: 11, name: "熱", price_delta: 0, available: true, remaining: null },
            { id: 12, name: "冰", price_delta: 0, available: true, remaining: null },
          ],
        },
        {
          id: 2,
          name: "加購",
          min_select: 0,
          max_select: 2,
          options: [
            { id: 21, name: "燕麥奶", price_delta: 20, available: true, remaining: null },
            { id: 22, name: "濃縮", price_delta: 30, available: true, remaining: 3 },
            { id: 23, name: "香草", price_delta: 15, available: false, remaining: null },
          ],
        },
      ],
    },
    {
      id: 6,
      name: "戚風",
      description: null,
      category_id: 1,
      unit_price: 90,
      photo: null,
      available: true,
      remaining: 2,
      option_groups: [],
    },
    {
      id: 7,
      name: "司康",
      description: null,
      category_id: 1,
      unit_price: 80,
      photo: null,
      available: true,
      remaining: 0,
      option_groups: [],
    },
  ],
};

describe("送單驗價", () => {
  it("價格由伺服器算：底價＋選項加價；選項依菜單順序寫進品名", () => {
    const r = priceOrder(MENU, [{ item_id: 5, option_ids: [21, 12], qty: 2 }]);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.total).toBe(340);
    expect(r.lines).toEqual([
      {
        item_id: 5,
        name: "拿鐵（冰、燕麥奶）",
        option_ids: [12, 21],
        unit_price: 170,
        qty: 2,
        line_total: 340,
        limited: false,
      },
    ]);
    expect(r.needsHold).toBe(false);
  });

  it("含每日限量品項或選項：標記要先確認庫存", () => {
    const item = priceOrder(MENU, [{ item_id: 6, option_ids: [], qty: 1 }]);
    const option = priceOrder(MENU, [{ item_id: 5, option_ids: [11, 22], qty: 1 }]);
    expect(item.ok && item.needsHold).toBe(true);
    expect(option.ok && option.needsHold).toBe(true);
  });

  it.each([
    ["沒有的品項", [{ item_id: 99, option_ids: [], qty: 1 }], "item_not_found"],
    ["售完的品項", [{ item_id: 7, option_ids: [], qty: 1 }], "sold_out"],
    ["超過剩餘份數", [{ item_id: 6, option_ids: [], qty: 3 }], "sold_out"],
    ["必選沒選", [{ item_id: 5, option_ids: [], qty: 1 }], "invalid_options"],
    ["單選選了兩個", [{ item_id: 5, option_ids: [11, 12], qty: 1 }], "invalid_options"],
    ["別的品項的選項", [{ item_id: 6, option_ids: [11], qty: 1 }], "invalid_options"],
    ["同一選項兩次", [{ item_id: 5, option_ids: [11, 21, 21], qty: 1 }], "invalid_options"],
    ["停售的選項", [{ item_id: 5, option_ids: [11, 23], qty: 1 }], "sold_out"],
    ["數量 0", [{ item_id: 6, option_ids: [], qty: 0 }], "invalid_qty"],
    ["單行超過 10 份", [{ item_id: 5, option_ids: [11], qty: 11 }], "invalid_qty"],
    ["數量不是整數", [{ item_id: 5, option_ids: [11], qty: 1.5 }], "invalid_qty"],
  ])("%s → 拒收", (_, lines, reason) => {
    const r = priceOrder(MENU, lines);
    expect(r).toMatchObject({ ok: false, reason });
  });

  it("超過 20 行或總計 50 份：拒收", () => {
    const many = Array.from({ length: 21 }, () => ({ item_id: 5, option_ids: [11], qty: 1 }));
    expect(priceOrder(MENU, many)).toMatchObject({ ok: false, reason: "too_many" });
    const heavy = Array.from({ length: 6 }, () => ({ item_id: 5, option_ids: [11], qty: 9 }));
    expect(priceOrder(MENU, heavy)).toMatchObject({ ok: false, reason: "too_many" });
  });

  it("空的單：拒收", () => {
    expect(priceOrder(MENU, [])).toMatchObject({ ok: false, reason: "empty" });
  });

  it("同一品項同選項分兩行：限量判斷看合計份數", () => {
    const r = priceOrder(MENU, [
      { item_id: 6, option_ids: [], qty: 1 },
      { item_id: 6, option_ids: [], qty: 2 },
    ]);
    expect(r).toMatchObject({ ok: false, reason: "sold_out" });
  });
});
