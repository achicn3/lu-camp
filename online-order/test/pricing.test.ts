// 送單驗價（docs/44 §3.2、§8.1 T4、§8.2）：價格只在伺服器端依目前菜單算；不合法的品項／選項／數量一律拒收。
import { describe, expect, it } from "vitest";

import type { MenuExperienceView, MenuSnapshot } from "../src/client/types";
import { minimumShortfall, priceOrder } from "../src/pricing";

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

// ── 手沖體驗卡（docs/63 §4、M1c）：原品項＋預選選項，價格照原品項算，品名標示體驗 ──
const EXPERIENCE: MenuExperienceView = {
  id: 9, item_id: 5, option_ids: [11], title: "熱拿鐵體驗", tag: null, origin: null, notes: null,
  description: null, includes: [], theme: "honey", art: "none", effect: "random",
};
const WITH_EXPERIENCE: MenuSnapshot = { ...MENU, experiences: [EXPERIENCE] };

describe("手沖體驗卡送單", () => {
  it("照原品項＋選項計價；品名帶體驗名稱，並記下是哪張體驗卡", () => {
    const r = priceOrder(WITH_EXPERIENCE, [{ item_id: 5, option_ids: [11, 21], qty: 1, experience_id: 9 }]);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.total).toBe(170);
    expect(r.lines[0]).toMatchObject({
      item_id: 5, option_ids: [11, 21], unit_price: 170, experience_id: 9,
      name: "熱拿鐵體驗・拿鐵（熱、燕麥奶）",
      // 客人看到的只有卡片名稱（店主 2026-10-17）；客人另外選的（燕麥奶）接在後面，預選的不重複。
      guest_name: "熱拿鐵體驗（燕麥奶）",
    });
  });

  it("沒有另外選：客人看到的就是卡片名稱；續點冠「續點・」", () => {
    const r = priceOrder(WITH_EXPERIENCE, [{ item_id: 5, option_ids: [11], qty: 1, experience_id: 9 }]);
    expect(r.ok && r.lines[0]!.guest_name).toBe("熱拿鐵體驗");
    const refillable: MenuSnapshot = {
      ...WITH_EXPERIENCE, refill_pct: 80,
      items: WITH_EXPERIENCE.items.map((i) => (i.id === 5 ? { ...i, refillable: true } : i)),
    };
    const refill = priceOrder(refillable, [{ item_id: 5, option_ids: [11], qty: 1, experience_id: 9, refill: true }]);
    expect(refill.ok && refill.lines[0]!.guest_name).toBe("續點・熱拿鐵體驗");
  });

  it.each([
    ["少了預選的選項", { item_id: 5, option_ids: [12], qty: 1, experience_id: 9 }],
    ["體驗卡和品項對不上", { item_id: 6, option_ids: [], qty: 1, experience_id: 9 }],
    ["沒有這張體驗卡", { item_id: 5, option_ids: [11], qty: 1, experience_id: 99 }],
  ])("拒收：%s", (_, line) => {
    expect(priceOrder(WITH_EXPERIENCE, [line])).toMatchObject({ ok: false, reason: "invalid_experience" });
  });

  it("一般品項的明細不多出體驗欄位", () => {
    const r = priceOrder(WITH_EXPERIENCE, [{ item_id: 6, option_ids: [], qty: 1 }]);
    expect(r.ok && Object.hasOwn(r.lines[0]!, "experience_id")).toBe(false);
    expect(r.ok && Object.hasOwn(r.lines[0]!, "guest_name")).toBe(false);
  });
});

describe("續點（店主 2026-10-16）", () => {
  const REFILL: MenuSnapshot = {
    ...MENU,
    refill_pct: 80,
    items: [
      { ...MENU.items[0]!, refillable: true },
      MENU.items[1]!,
      { ...MENU.items[0]!, id: 8, name: "冷泡茶", unit_price: 10, option_groups: [], refillable: true },
    ],
  };

  it("照折數算（加料也打折）、一杯一杯四捨五入；品名冠「續點・」並標記續點", () => {
    const r = priceOrder(REFILL, [{ item_id: 5, option_ids: [12, 21], qty: 2, refill: true }]);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.lines).toEqual([
      {
        item_id: 5, name: "續點・拿鐵（冰、燕麥奶）", option_ids: [12, 21],
        unit_price: 136, qty: 2, line_total: 272, limited: false, refill: true,
      },
    ]);
    const half = priceOrder({ ...REFILL, refill_pct: 85 }, [{ item_id: 8, option_ids: [], qty: 1, refill: true }]);
    expect(half.ok && half.lines[0]!.unit_price).toBe(9); // 10 × 85% = 8.5 → 9
  });

  it("續點和一般點是不同行；一般點不多出續點欄位", () => {
    const r = priceOrder(REFILL, [
      { item_id: 5, option_ids: [11], qty: 1 },
      { item_id: 5, option_ids: [11], qty: 1, refill: true },
    ]);
    expect(r.ok && r.lines.map((l) => [l.name, l.unit_price])).toEqual([["拿鐵（熱）", 150], ["續點・拿鐵（熱）", 120]]);
    expect(r.ok && Object.hasOwn(r.lines[0]!, "refill")).toBe(false);
  });

  it("品項不可續點、或菜單沒有折數：拒收", () => {
    expect(priceOrder(REFILL, [{ item_id: 6, option_ids: [], qty: 1, refill: true }]))
      .toMatchObject({ ok: false, reason: "not_refillable", item_id: 6 });
    const { refill_pct: _, ...noPct } = REFILL;
    expect(priceOrder(noPct, [{ item_id: 5, option_ids: [11], qty: 1, refill: true }]))
      .toMatchObject({ ok: false, reason: "not_refillable" });
  });

  it("低消不算續點：金額與每人份數都扣掉續點", () => {
    const lines = [
      { item_id: 5, qty: 1, line_total: 150 },
      { item_id: 5, qty: 2, line_total: 240, refill: true as const },
    ];
    expect(minimumShortfall({ mode: "amount", amount: 300 }, 390, lines, null)).toEqual({ missing_amount: 150 });
    expect(minimumShortfall({ mode: "per_person_item" }, 390, lines, 2)).toEqual({ missing_items: 1 });
  });
});
