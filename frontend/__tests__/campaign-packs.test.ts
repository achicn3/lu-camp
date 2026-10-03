// 組合包袋裝條碼（ADR-028）：從組合價格子整理出可以放進袋子的商品、送出的內容。
import { describe, expect, it } from "vitest";

import { packCandidates, packItems, packUnitsRequired } from "@/features/campaigns/packs";

const slots = [
  {
    slot_no: 0,
    qty: 6,
    targets: [
      { target_type: "CATALOG_PRODUCT" as const, target_id: 7, label: "天堂鳥濾掛" },
      { target_type: "CATALOG_PRODUCT" as const, target_id: 8, label: "蜜桃濾掛" },
    ],
  },
  {
    slot_no: 1,
    qty: 6,
    targets: [
      { target_type: "CATALOG_PRODUCT" as const, target_id: 8, label: "蜜桃濾掛" },
      { target_type: "BULK_BASKET" as const, target_id: 5, label: "營釘籃" },
      { target_type: "SERIALIZED_ITEM" as const, target_id: 21, label: "帳篷（S1-AAA）" },
      { target_type: "CATEGORY" as const, target_id: 3, label: "露營燈" },
    ],
  },
];

describe("packCandidates", () => {
  it("把格子裡指定到單一商品的範圍整理成可放的商品（去重、保留順序）；分類／品牌另外標出", () => {
    expect(packCandidates(slots)).toEqual({
      items: [
        { key: "CATALOG:7", item_type: "CATALOG", target_id: 7, label: "天堂鳥濾掛" },
        { key: "CATALOG:8", item_type: "CATALOG", target_id: 8, label: "蜜桃濾掛" },
        { key: "BULK_BASKET:5", item_type: "BULK_BASKET", target_id: 5, label: "營釘籃" },
        { key: "SERIALIZED:21", item_type: "SERIALIZED", target_id: 21, label: "帳篷（S1-AAA）" },
      ],
      broadTargets: ["露營燈"],
    });
  });

  it("一組共要幾件", () => {
    expect(packUnitsRequired(slots)).toBe(12);
  });
});

describe("packItems", () => {
  it("只送有填件數的；件數不是正整數的忽略", () => {
    const candidates = packCandidates(slots).items;
    expect(
      packItems(candidates, { "CATALOG:7": "6", "CATALOG:8": "6", "BULK_BASKET:5": "0", "SERIALIZED:21": "x" }),
    ).toEqual([
      { item_type: "CATALOG", target_id: 7, qty: 6 },
      { item_type: "CATALOG", target_id: 8, qty: 6 },
    ]);
  });
});
