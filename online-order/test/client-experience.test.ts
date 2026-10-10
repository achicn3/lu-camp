// 手沖體驗卡與加購推薦的純邏輯（docs/63 §4、§6；M1c）。
import { describe, expect, it } from "vitest";

import { experienceView, upsellSuggestions } from "../src/client/logic";
import type { MenuExperienceView, MenuItemView, MenuSnapshot, UpsellRole } from "../src/client/types";

function item(id: number, overrides: Partial<MenuItemView> = {}, role?: UpsellRole): MenuItemView {
  return {
    id, name: `品項${id}`, description: null, category_id: 1, unit_price: 100, photo: null,
    available: true, remaining: null, option_groups: [],
    ...(role ? { presentation: {
      flavor_description: null, audience_description: null, is_new: false,
      limited_on: null, show_remaining: true, low_stock_threshold: 5, hide_sold_out: false, role,
    } } : {}),
    ...overrides,
  };
}

const BREW = item(1, { name: "手沖咖啡", unit_price: 220, option_groups: [
  { id: 10, name: "豆子", min_select: 1, max_select: 1, options: [
    { id: 11, name: "蜜桃蹦蹦", price_delta: 60, available: true, remaining: null },
    { id: 12, name: "天堂鳥", price_delta: 20, available: true, remaining: 0 },
  ] },
  { id: 20, name: "溫度", min_select: 1, max_select: 1, options: [
    { id: 21, name: "熱", price_delta: 0, available: true, remaining: null },
    { id: 22, name: "冰", price_delta: 10, available: true, remaining: null },
  ] },
  { id: 30, name: "加購", min_select: 0, max_select: 1, options: [
    { id: 31, name: "燕麥奶", price_delta: 20, available: true, remaining: null },
  ] },
] }, "experience");

function exp(overrides: Partial<MenuExperienceView> = {}): MenuExperienceView {
  return {
    id: 5, item_id: 1, option_ids: [11], title: "蜜桃蹦蹦手沖體驗", tag: null, origin: null, notes: null,
    description: null, includes: [], theme: "peach", art: "peach", effect: "random", ...overrides,
  };
}

function menu(items: MenuItemView[], experiences: MenuExperienceView[] = []): MenuSnapshot {
  return { version: 1, published_at: "", store_name: "露坑", font: null, categories: [], items, experiences };
}

describe("手沖體驗卡", () => {
  it("價格＝原品項＋預選選項；還沒選的必選群組要客人補選，選完價格可能再加（標「起」）", () => {
    const view = experienceView(menu([BREW]), exp());
    expect(view).toMatchObject({ price: 280, priceFrom: true, soldOut: false });
    expect(view?.pending.map((g) => g.id)).toEqual([20]);
  });

  it("預選把必選都選好了、剩下的必選群組沒有加價：價格固定，不標「起」", () => {
    const view = experienceView(menu([BREW]), exp({ option_ids: [11, 21] }));
    expect(view).toMatchObject({ price: 280, priceFrom: false });
    expect(view?.pending).toEqual([]);
  });

  it("預選的豆子售完、或原品項停售：體驗也不能點", () => {
    expect(experienceView(menu([BREW]), exp({ option_ids: [12] }))?.soldOut).toBe(true);
    expect(experienceView(menu([{ ...BREW, available: false }]), exp())?.soldOut).toBe(true);
  });

  it("原品項不在菜單上：不顯示這張卡", () => {
    expect(experienceView(menu([]), exp())).toBeNull();
  });
});

describe("加購推薦", () => {
  const latte = item(2, { name: "拿鐵" }, "coffee");
  const cake = item(3, { name: "戚風" }, "dessert");
  const scone = item(4, { name: "司康" }, "dessert");
  const tart = item(5, { name: "塔" }, "dessert");
  const soldOutCake = item(6, { name: "售完的蛋糕", remaining: 0 }, "dessert");
  const all = menu([latte, soldOutCake, cake, scone, tart]);

  it("咖啡配甜點：照菜單順序、略過售完的，最多 2 項", () => {
    const picks = upsellSuggestions(all, [{ item_id: 2, option_ids: [], qty: 1 }], new Set());
    expect(picks.map((p) => p.id)).toEqual([3, 4]);
  });

  it("已在購物車的不推；客人略過過的方向不再推", () => {
    const cart = [{ item_id: 2, option_ids: [], qty: 1 }, { item_id: 3, option_ids: [], qty: 1 }];
    expect(upsellSuggestions(all, cart, new Set()).map((p) => p.id)).toEqual([4, 5]);
    expect(upsellSuggestions(all, cart, new Set(["dessert"]))).toEqual([]);
  });

  it("甜點配咖啡；沒設角色的品項不推、也不觸發推薦", () => {
    expect(upsellSuggestions(all, [{ item_id: 3, option_ids: [], qty: 1 }], new Set()).map((p) => p.id)).toEqual([2]);
    const plain = menu([item(9), latte, cake]);
    expect(upsellSuggestions(plain, [{ item_id: 9, option_ids: [], qty: 1 }], new Set())).toEqual([]);
  });

  it("從體驗卡點的：推咖啡豆／濾掛（這一波還沒有零售品時就不推）", () => {
    const withBeans = menu([BREW, item(7, { name: "掛耳" }, "drip"), latte]);
    const cart = [{ item_id: 1, option_ids: [11, 21], qty: 1, experience_id: 5 }];
    expect(upsellSuggestions(withBeans, cart, new Set()).map((p) => p.id)).toEqual([7]);
    expect(upsellSuggestions(menu([BREW, latte]), cart, new Set())).toEqual([]);
  });
});
