// 客人頁的帶回家商品（docs/63 §13、M1d）：依分類分組、購物車合併、加購推咖啡豆／濾掛。
import { describe, expect, it } from "vitest";

import { addLine, checkCart } from "../src/client/cart";
import { retailGroups, retailSoldOut, upsellSuggestions } from "../src/client/logic";
import type { MenuItemView, MenuRetailView, MenuSnapshot } from "../src/client/types";

function product(id: number, overrides: Partial<MenuRetailView> = {}): MenuRetailView {
  return {
    id, name: `商品${id}`, description: null, category: "咖啡豆", unit_price: 450, photo: null,
    role: null, available: true, remaining: 5, ...overrides,
  };
}

const BREW: MenuItemView = {
  id: 1, name: "手沖咖啡", description: null, category_id: 1, unit_price: 220, photo: null,
  available: true, remaining: null, option_groups: [],
  presentation: {
    flavor_description: null, audience_description: null, is_recommended: false, is_new: false,
    limited_on: null, show_remaining: true, low_stock_threshold: 5, hide_sold_out: false, role: "experience",
  },
};

function menu(retail: MenuRetailView[], items: MenuItemView[] = []): MenuSnapshot {
  return { version: 1, published_at: "", store_name: "露坑", font: null, categories: [], items, retail };
}

describe("帶回家分組", () => {
  it("依商品分類分組、照快照順序；沒分類的放「其他」最後", () => {
    const groups = retailGroups(menu([
      product(1, { category: "濾掛" }), product(2), product(3, { category: null }), product(4, { category: "濾掛" }),
    ]));
    expect(groups.map((g) => [g.category, g.products.map((p) => p.id)])).toEqual([
      ["濾掛", [1, 4]], ["咖啡豆", [2]], ["其他", [3]],
    ]);
  });

  it("停售或現量 0：售完", () => {
    expect(retailSoldOut(product(1, { remaining: 0 }))).toBe(true);
    expect(retailSoldOut(product(1, { available: false }))).toBe(true);
    expect(retailSoldOut(product(1))).toBe(false);
  });
});

describe("購物車", () => {
  it("同一個商品合併數量；超過現量擋下", () => {
    const m = menu([product(41, { remaining: 3 })]);
    const cart = addLine(m, addLine(m, [], { catalog_product_id: 41, qty: 1 }), { catalog_product_id: 41, qty: 2 });
    expect(cart).toEqual([{ catalog_product_id: 41, qty: 3 }]);
    expect(checkCart(m, cart)).toMatchObject({ ok: true, total: 1350 });
    expect(() => addLine(m, cart, { catalog_product_id: 41, qty: 1 })).toThrow("sold_out");
  });
});

describe("加購", () => {
  it("點了體驗卡：推可售的咖啡豆／濾掛；已在車裡、售完的不推", () => {
    const bean = product(41, { name: "耶加雪菲", role: "bean" });
    const drip = product(42, { name: "濾掛", role: "drip" });
    const gone = product(43, { role: "drip", remaining: 0 });
    const m = menu([gone, bean, drip], [BREW]);
    const fromCard = [{ item_id: 1, option_ids: [], qty: 1, experience_id: 5 }];
    expect(upsellSuggestions(m, fromCard, new Set()).map((p) => p.name)).toEqual(["耶加雪菲", "濾掛"]);
    const withBean = [...fromCard, { catalog_product_id: 41, qty: 1 }];
    // 已買豆子：豆→濾掛／其他豆款，但這包已在車裡
    expect(upsellSuggestions(m, withBean, new Set()).map((p) => p.name)).toEqual(["濾掛"]);
    expect(upsellSuggestions(m, withBean, new Set(["bean", "drip"]))).toEqual([]);
  });
});
