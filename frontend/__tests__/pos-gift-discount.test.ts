import { describe, expect, it } from "vitest";

import {
  addLine,
  type CartLine,
  isGift,
  markAsGift,
  removeLine,
  toSaleLines,
  unmarkGift,
} from "@/features/pos/cart";
import {
  describeDiscount,
  type DiscountDraft,
  pruneDiscounts,
  toAdjustmentRequests,
} from "@/features/pos/discounts";

function line(key: string, description: string, id: number): CartLine {
  return {
    key,
    lineType: "CATALOG",
    description,
    unitPrice: 300,
    qty: 1,
    catalogProductId: id,
  };
}

function draft(overrides: Partial<DiscountDraft> = {}): DiscountDraft {
  return {
    id: "d1",
    scope: "ORDER",
    targetKey: null,
    method: "FIXED_AMOUNT",
    value: 100,
    reasonId: null,
    note: null,
    ...overrides,
  };
}

describe("購物車贈品", () => {
  it("改為贈品後 key 會換前綴，同商品的買與送才能並存為兩列", () => {
    const bought = line("C:5", "小物", 5);
    const marked = markAsGift([bought], "C:5", { reasonId: 7, note: "週年慶" });
    expect(marked[0]!.key).toBe("G:C:5");
    expect(isGift(marked[0]!)).toBe(true);

    // 再加入同一商品的一般銷售：key 不同 → 兩列並存，不會被合併
    const { lines } = addLine(marked, bought);
    expect(lines).toHaveLength(2);
    expect(lines.map((l) => l.lineKind ?? "NORMAL")).toEqual([
      "GIFT",
      "NORMAL",
    ]);
  });

  it("贈品送出的 payload 帶原因，取消贈品則清掉原因", () => {
    const marked = markAsGift([line("C:5", "小物", 5)], "C:5", {
      reasonId: 7,
      note: "週年慶",
    });
    expect(toSaleLines(marked)[0]).toMatchObject({
      line_kind: "GIFT",
      gift_reason_id: 7,
      gift_note: "週年慶",
    });

    const reverted = unmarkGift(marked, "G:C:5");
    expect(reverted[0]!.key).toBe("C:5");
    expect(toSaleLines(reverted)[0]).toMatchObject({
      line_kind: "NORMAL",
      gift_reason_id: null,
      gift_note: null,
    });
  });
});

describe("臨時折扣草稿", () => {
  it("送出時才把購物車 key 換算成明細索引", () => {
    const lines = [line("C:1", "甲", 1), line("C:2", "乙", 2)];
    const drafts = [
      draft({ id: "a", scope: "ITEM", targetKey: "C:2", value: 50 }),
      draft({ id: "b", scope: "ORDER", value: 20, method: "PERCENTAGE" }),
    ];
    expect(toAdjustmentRequests(drafts, lines)).toEqual([
      {
        scope: "ITEM",
        method: "FIXED_AMOUNT",
        value: "50",
        target_line_index: 1,
        reason_id: null,
        note: null,
      },
      {
        scope: "ORDER",
        method: "PERCENTAGE",
        value: "20",
        target_line_index: null,
        reason_id: null,
        note: null,
      },
    ]);
  });

  it("移除前面的商品後，折扣仍跟著原本那一件走", () => {
    // 這是存索引會出錯的情形：折扣原本指向索引 1，移除第一列後索引 1 已是別的商品。
    const lines = [line("C:1", "甲", 1), line("C:2", "乙", 2)];
    const drafts = [draft({ scope: "ITEM", targetKey: "C:2", value: 50 })];
    const after = removeLine(lines, "C:1");
    expect(toAdjustmentRequests(drafts, after)[0]!.target_line_index).toBe(0);
  });

  it("目標商品被移除時，該筆折扣一併作廢", () => {
    const lines = [line("C:1", "甲", 1)];
    const drafts = [
      draft({ id: "a", scope: "ITEM", targetKey: "C:9", value: 50 }),
      draft({ id: "b", scope: "ORDER", value: 10 }),
    ];
    expect(pruneDiscounts(drafts, lines).map((d) => d.id)).toEqual(["b"]);
    expect(toAdjustmentRequests(drafts, lines)).toHaveLength(1);
  });

  it("折扣說明讓店員看得懂折在哪裡", () => {
    const lines = [line("C:1", "甲", 1)];
    expect(
      describeDiscount(
        draft({ scope: "ITEM", targetKey: "C:1", value: 15, method: "PERCENTAGE" }),
        lines,
      ),
    ).toBe("甲 15%");
    expect(describeDiscount(draft({ value: 100 }), lines)).toBe(
      "整單折扣 −100 元",
    );
  });
});
