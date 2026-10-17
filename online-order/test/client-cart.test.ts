import { describe, expect, it } from "vitest";

import {
  addLine, CART_IDLE_MS, changeQty, checkCart, parseSavedCart, removeLine, resumeCart, type CartLine, type SavedCart,
} from "../src/client/cart";
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

describe("guest cart with refills", () => {
  const refillMenu: MenuSnapshot = { ...menu, refill_pct: 80, items: [{ ...menu.items[0]!, remaining: null, refillable: true }] };
  it("keeps a refill apart from the same drink ordered normally and merges refills", () => {
    const hot: CartLine = { item_id: 1, option_ids: [5], qty: 1 };
    const refill: CartLine = { ...hot, refill: true };
    const cart = addLine(refillMenu, addLine(refillMenu, [hot], refill), refill);
    expect(cart).toEqual([hot, { ...refill, qty: 2 }]);
    expect(checkCart(refillMenu, cart)).toMatchObject({ ok: true, total: 100 + 80 * 2 });
  });
});

// 店主 2026-10-17：同一支手機換桌或隔了很久再掃碼，不該看到上一輪沒送出的購物車；
// 但客人還在點的時候絕不能清掉——所以只看「最後一次動購物車」到現在，不做固定週期清空。
describe("購物車沿用規則", () => {
  const t0 = Date.UTC(2026, 9, 17, 4, 0);
  const saved: SavedCart = { lines: [cold], table: "A1", touched_at: t0 };

  it("同一桌、最後一次動購物車還不到時限：照樣沿用", () => {
    expect(resumeCart(saved, "A1", t0 + CART_IDLE_MS - 1)).toEqual({ lines: [cold], expired: false });
  });

  it("放著沒動超過時限：清空並告知", () => {
    expect(resumeCart(saved, "A1", t0 + CART_IDLE_MS)).toEqual({ lines: [], expired: true });
  });

  it("掃了另一桌（或外帶）的碼：不管多久都清空", () => {
    expect(resumeCart(saved, "B2", t0 + 1000)).toEqual({ lines: [], expired: true });
    expect(resumeCart(saved, null, t0 + 1000)).toEqual({ lines: [], expired: true });
  });

  it("訂單頁不比桌號（網址沒有桌號），只看時限", () => {
    expect(resumeCart(saved, undefined, t0 + 1000)).toEqual({ lines: [cold], expired: false });
    expect(resumeCart(saved, undefined, t0 + CART_IDLE_MS)).toEqual({ lines: [], expired: true });
  });

  it("本來就是空的購物車不算「被清空」", () => {
    expect(resumeCart({ ...saved, lines: [] }, "B2", t0 + CART_IDLE_MS)).toEqual({ lines: [], expired: false });
    expect(resumeCart(null, "A1", t0)).toEqual({ lines: [], expired: false });
  });

  it("手機時間被往回調（最後一次動在未來）：當作過期，不無限期沿用", () => {
    expect(resumeCart(saved, "A1", t0 - 60_000)).toEqual({ lines: [], expired: true });
  });
});

describe("讀回存起來的購物車", () => {
  const t0 = Date.UTC(2026, 9, 17, 4, 0);
  it("讀得回桌號、時間與各種品項行", () => {
    const lines: CartLine[] = [cold, { catalog_product_id: 9, qty: 2 }, { item_id: 1, option_ids: [5], qty: 1, experience_id: 7 }];
    expect(parseSavedCart(JSON.stringify({ lines, table: "A1", touched_at: t0 }))).toEqual({ lines, table: "A1", touched_at: t0 });
    expect(parseSavedCart(JSON.stringify({ lines, table: null, touched_at: t0 }))?.table).toBeNull();
  });

  it("壞掉的資料一律當沒有；壞掉的行只丟那一行", () => {
    expect(parseSavedCart(null)).toBeNull();
    expect(parseSavedCart("{")).toBeNull();
    expect(parseSavedCart(JSON.stringify([cold]))).toBeNull(); // 舊版只存陣列、沒有時間：當過期
    expect(parseSavedCart(JSON.stringify({ lines: [cold], table: "A1" }))).toBeNull();
    expect(parseSavedCart(JSON.stringify({ lines: [cold, { item_id: "x" }], table: "A1", touched_at: t0 }))?.lines).toEqual([cold]);
  });
});
