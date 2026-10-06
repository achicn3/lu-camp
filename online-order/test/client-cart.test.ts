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
