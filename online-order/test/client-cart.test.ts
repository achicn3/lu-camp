import { describe, expect, it } from "vitest";

import { addLine, changeQty, checkCart, removeLine, type CartLine } from "../src/client/cart";
import type { MenuSnapshot } from "../src/client/types";

const menu: MenuSnapshot = {
  version: 1, published_at: "", store_name: "露坑", font: null, categories: [],
  items: [{
    id: 1, name: "咖啡", description: null, category_id: null, unit_price: 100, photo: null,
    available: true, remaining: 3,
    option_groups: [{ id: 4, name: "溫度", min_select: 1, max_select: 1, options: [
      { id: 5, name: "熱", price_delta: 0, available: true, remaining: null },
      { id: 6, name: "冰", price_delta: 20, available: true, remaining: 2 },
    ] }],
  }],
};
const cold: CartLine = { item_id: 1, option_ids: [6], qty: 1 };

describe("guest cart", () => {
  it("requires options and uses server pricing for total", () => {
    expect(checkCart(menu, [{ item_id: 1, option_ids: [], qty: 1 }]).ok).toBe(false);
    const cart = addLine(menu, [], cold);
    expect(cart).toEqual([cold]);
    expect(checkCart(menu, cart)).toMatchObject({ ok: true, total: 120 });
  });

  it("merges matching choices, edits quantity, removes a line, and respects stock", () => {
    const twice = addLine(menu, [cold], cold);
    expect(twice).toEqual([{ ...cold, qty: 2 }]);
    expect(() => addLine(menu, twice, cold)).toThrow();
    expect(changeQty(menu, twice, 0, 1)).toEqual([cold]);
    expect(removeLine(twice, 0)).toEqual([]);
  });
});

describe("guest cart with experiences", () => {
  const withExp: MenuSnapshot = { ...menu, experiences: [{
    id: 7, item_id: 1, option_ids: [5], title: "熱咖啡體驗", tag: null, origin: null, notes: null,
    description: null, includes: [], theme: "ink", art: "none", effect: "random",
  }] };
  const hotPlain: CartLine = { item_id: 1, option_ids: [5], qty: 1 };
  const hotExp: CartLine = { item_id: 1, option_ids: [5], qty: 1, experience_id: 7 };

  it("keeps an experience line apart from the same plain item and merges repeats", () => {
    const cart = addLine(withExp, addLine(withExp, [hotPlain], hotExp), hotExp);
    expect(cart).toEqual([hotPlain, { ...hotExp, qty: 2 }]);
  });
});
