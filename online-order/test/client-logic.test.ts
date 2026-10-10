// 客人點餐頁的純邏輯（docs/44 §4.2；店主 2026-10-02 定案的問候語與顯示方式）。
import { describe, expect, it } from "vitest";

import type { MenuExperienceView, MenuItemView, MenuPresentation, MenuRetailView, MenuSnapshot } from "../src/client/types";
import { greeting, itemBadge, itemSoldOut, menuTabs, presentationBadges, staffPicks, visibleItems, priceText, tableCodeFromPath } from "../src/client/logic";

// 台北時間 = UTC+8
const taipei = (hh: number, mm = 0) => new Date(Date.UTC(2026, 9, 2, hh - 8, mm));

describe("時段問候", () => {
  it.each([
    [5, "早安，今天想喝點什麼？"],
    [10, "早安，今天想喝點什麼？"],
    [11, "午安，下午想來點什麼？"],
    [16, "午安，下午想來點什麼？"],
    [17, "晚安，今晚想來點什麼？"],
    [23, "晚安，今晚想來點什麼？"],
    [2, "晚安，今晚想來點什麼？"],
  ])("台北 %i 點：%s", (hour, text) => {
    expect(greeting(taipei(hour))).toBe(text);
  });

  it("用台北時間判斷，不看手機所在時區", () => {
    // UTC 02:00 = 台北 10:00（早上），不論手機設定在哪個時區
    expect(greeting(new Date(Date.UTC(2026, 9, 2, 2, 0)))).toBe("早安，今天想喝點什麼？");
  });
});

describe("價格", () => {
  it("沒有選項：直接顯示價格；有選項：加「起」", () => {
    expect(priceText({ unit_price: 150, option_groups: [] })).toBe("$150");
    expect(priceText({ unit_price: 150, option_groups: [{}] })).toBe("$150 起");
    expect(priceText({ unit_price: 1200, option_groups: [] })).toBe("$1,200");
  });
});

describe("售完／剩幾份", () => {
  it.each([
    [null, null],
    [0, "今日售完"],
    [1, "最後 1 份"],
    [3, "今天剩 3 份"],
    [5, "今天剩 5 份"],
    [6, null],
  ])("remaining=%s → %s", (remaining, badge) => {
    expect(itemBadge({ remaining })).toBe(badge);
  });
});

describe("桌位碼", () => {
  it("從 /t/<碼> 取出；其他路徑沒有碼", () => {
    expect(tableCodeFromPath("/t/abcDEF123_-xyz789")).toBe("abcDEF123_-xyz789");
    expect(tableCodeFromPath("/t/abcDEF123_-xyz789/")).toBe("abcDEF123_-xyz789");
    expect(tableCodeFromPath("/")).toBeNull();
    expect(tableCodeFromPath("/t/")).toBeNull();
    expect(tableCodeFromPath("/t/has space")).toBeNull();
  });
});

const presentation: MenuPresentation = {
  flavor_description: null, audience_description: null,
  is_new: false, limited_on: null,
  show_remaining: true, low_stock_threshold: 5, hide_sold_out: false,
};
const item: MenuItemView = {
  id: 1, name: "拿鐵", description: null, category_id: 1, unit_price: 150,
  photo: null, available: true, remaining: 3, option_groups: [],
};

describe("呈現庫存與可售判斷", () => {
  it("隱藏數量仍顯示售完，自訂門檻不改原始庫存", () => {
    const hidden = { ...item, presentation: { ...presentation, show_remaining: false } };
    expect(itemBadge(hidden)).toBeNull();
    expect(itemBadge({ ...hidden, remaining: 0 })).toBe("今日售完");
    expect(itemBadge({ ...item, presentation: { ...presentation, low_stock_threshold: 2 } })).toBeNull();
    expect(itemBadge({ ...item, remaining: 1, presentation: { ...presentation, low_stock_threshold: 0 } })).toBeNull();
    expect(hidden.remaining).toBe(3);
  });
});

describe("人工標籤", () => {
  it("未設定不杜撰；今日限定在台北跨日自動失效", () => {
    expect(presentationBadges(item, new Date("2026-10-06T15:59:59Z"))).toEqual([]);
    // 「露坑推薦」標籤已改成「店員推薦」分頁（2026-10-10）
    const featured = { ...item, presentation: { ...presentation, is_new: true, limited_on: "2026-10-06" } };
    expect(presentationBadges(featured, new Date("2026-10-06T15:59:59Z"))).toEqual(["新品", "今日限定"]);
    expect(presentationBadges(featured, new Date("2026-10-06T16:00:00Z"))).toEqual(["新品"]);
    expect(presentationBadges(featured, new Date("2026-10-05T15:59:59Z"))).toEqual(["新品"]);
  });
});

const required = { id: 2, name: "豆種", min_select: 1, max_select: 1,
  options: [{ id: 7, name: "豆 A", price_delta: 10, available: true, remaining: 0 }],
};

describe("售完展示", () => {
  it("必選群組無足夠可售選項視為售完；可選群組不阻擋", () => {
    expect(itemSoldOut({ ...item, option_groups: [required] })).toBe(true);
    expect(itemSoldOut({ ...item, option_groups: [{ ...required, min_select: 0 }] })).toBe(false);
    expect(itemSoldOut({ ...item, option_groups: [{ ...required,
      options: [{ ...required.options[0]!, remaining: null }],
    }] })).toBe(false);
    expect(itemSoldOut({ ...item, option_groups: [{ ...required,
      min_select: 2, max_select: 2, options: [{ ...required.options[0]!, remaining: 2 }],
    }] })).toBe(true);
    expect(itemSoldOut({ ...item, available: false })).toBe(true);
  });

  it("每個分類維持可售順序，售完後置；隱藏售完不修改來源", () => {
    const items = [
      { ...item, id: 1, remaining: 0 },
      { ...item, id: 2, category_id: 2, remaining: 0 },
      { ...item, id: 3 },
      { ...item, id: 4, category_id: 2 },
      { ...item, id: 5 },
      { ...item, id: 6, option_groups: [required],
        presentation: { ...presentation, hide_sold_out: true } },
    ];
    expect(visibleItems(items).map((entry) => entry.id)).toEqual([3, 4, 5, 2, 1]);
    expect(visibleItems(items.filter((entry) => entry.category_id === 1)).map((entry) => entry.id))
      .toEqual([3, 5, 1]);
    expect(items.map((entry) => entry.id)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(items[0]?.remaining).toBe(0);
  });
});


describe("完整菜單的分類列（2026-10-10）", () => {
  const exp: MenuExperienceView = {
    id: 9, item_id: 1, option_ids: [], title: "手沖體驗", tag: null, origin: null, notes: null,
    description: null, includes: [], theme: "peach", art: "peach", effect: "random",
  };
  const bean: MenuRetailView = {
    id: 40, name: "咖啡豆", description: null, category: "豆", unit_price: 450, photo: null,
    role: "bean", available: true, remaining: 3,
  };
  const base: MenuSnapshot = {
    version: 1, published_at: "2026-10-10T00:00:00Z", store_name: "露坑", font: null,
    categories: [{ id: 1, name: "咖啡" }, { id: 2, name: "甜點" }],
    items: [item, { ...item, id: 2, category_id: 2 }],
  };

  it("店員推薦第一、手沖體驗、各分類、帶著走；沒有的就不出現", () => {
    const full = { ...base, experiences: [exp], retail: [bean], picks: [{ kind: "item" as const, id: 2 }] };
    expect(menuTabs(full).map((tab) => tab.label)).toEqual(["店員推薦", "手沖體驗", "咖啡", "甜點", "帶著走"]);
    expect(menuTabs(base).map((tab) => tab.label)).toEqual(["咖啡", "甜點"]);
  });

  it("沒分類的品項放「其他」，不會找不到", () => {
    const loose = { ...base, items: [...base.items, { ...item, id: 3, category_id: null }] };
    expect(menuTabs(loose).map((tab) => tab.label)).toEqual(["咖啡", "甜點", "其他"]);
  });

  it("沒有可展示品項的分類不出現", () => {
    const hidden = { ...base, items: [item, { ...item, id: 2, category_id: 2, remaining: 0,
      presentation: { ...presentation, hide_sold_out: true } }] };
    expect(menuTabs(hidden).map((tab) => tab.label)).toEqual(["咖啡"]);
  });

  it("店員推薦照店主排的順序；看不到的（隱藏、已不在菜單）略過；三種東西都能推", () => {
    const menu = { ...base, experiences: [exp], retail: [bean], picks: [
      { kind: "retail" as const, id: 40 }, { kind: "item" as const, id: 99 },
      { kind: "experience" as const, id: 9 }, { kind: "item" as const, id: 2 },
    ] };
    expect(staffPicks(menu).map((pick) => `${pick.kind}:${pick.kind === "item" ? pick.item.id :
      pick.kind === "experience" ? pick.view.experience.id : pick.product.id}`))
      .toEqual(["retail:40", "experience:9", "item:2"]);
    const none = { ...menu, picks: [{ kind: "item" as const, id: 99 }] };
    expect(staffPicks(none)).toEqual([]);
    expect(menuTabs(none).map((tab) => tab.label)[0]).toBe("手沖體驗");
  });
});

describe("人氣標籤（M2b）", () => {
  it("第一名人氣 No.1、二三名人氣推薦，排在人工標籤前面；沒上榜沒有", () => {
    const now = new Date("2026-10-06T08:00:00Z");
    expect(presentationBadges({ ...item, popularity: 1 }, now)).toEqual(["人氣 No.1"]);
    expect(presentationBadges({ ...item, popularity: 3 }, now)).toEqual(["人氣推薦"]);
    const featured = { ...item, popularity: 2, presentation: { ...presentation, is_new: true } };
    expect(presentationBadges(featured, now)).toEqual(["人氣推薦", "新品"]);
  });
});
