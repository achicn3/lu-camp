// POS 結帳改善（店主 2026-10-04）：備註後面加「-條碼末三碼」（店員靠末三碼找包裝放哪）、
// 購物車總件數。
import { describe, expect, it } from "vitest";

import {
  barcodeTail,
  basketCartLine,
  cartItemCount,
  type CartLine,
  lineNoteText,
  linesWithNotes,
  noteAckFingerprint,
  packCartLines,
} from "@/features/pos/cart";
import type { components } from "@/lib/api-types";

const base: CartLine = {
  key: "S:S1-9B5D254CAE",
  lineType: "SERIALIZED",
  description: "帳篷",
  unitPrice: 1000,
  qty: 1,
  itemCode: "S1-9B5D254CAE",
};

describe("barcodeTail", () => {
  it("取條碼最後三碼", () => {
    expect(barcodeTail("S1-9B5D254CAE")).toBe("CAE");
  });

  it("前後空白先修剪", () => {
    expect(barcodeTail("  4710001234567 ")).toBe("567");
  });

  it("不足三碼時整個條碼照列", () => {
    expect(barcodeTail("A1")).toBe("A1");
  });
});

describe("lineNoteText（備註＋條碼末三碼）", () => {
  it("序號品用 item_code 的末三碼", () => {
    expect(lineNoteText({ ...base, note: "缺營釘一支" })).toBe("缺營釘一支-CAE");
  });

  it("其他型態用掃到的條碼（barcode）", () => {
    const line: CartLine = {
      key: "C:7",
      lineType: "CATALOG",
      description: "瓦斯罐",
      unitPrice: 120,
      qty: 3,
      catalogProductId: 7,
      barcode: "SKU-000123",
      note: "效期較短",
    };
    expect(lineNoteText(line)).toBe("效期較短-123");
  });

  it("備註前後空白修剪後再接末三碼", () => {
    expect(lineNoteText({ ...base, note: "  缺充電線  " })).toBe("缺充電線-CAE");
  });

  it("沒有備註回 null（沒備註的商品不加末三碼）", () => {
    expect(lineNoteText(base)).toBeNull();
    expect(lineNoteText({ ...base, note: "   " })).toBeNull();
  });

  it("不知道條碼時只顯示備註，不硬湊", () => {
    expect(lineNoteText({ ...base, itemCode: undefined, note: "缺營釘" })).toBe("缺營釘");
  });
});

describe("linesWithNotes 帶末三碼與品牌", () => {
  it("結帳提醒的備註也接上末三碼，並帶出品牌 id", () => {
    expect(linesWithNotes([{ ...base, note: "缺營釘", brandId: 5 }])).toEqual([
      { key: base.key, description: "帳篷", note: "缺營釘-CAE", brandId: 5 },
    ]);
  });

  it("確認指紋只看原始備註：補上末三碼不會讓已確認的提醒失效", () => {
    const withCode = { ...base, note: "缺營釘" };
    expect(noteAckFingerprint([withCode])).toBe(
      noteAckFingerprint([{ ...withCode, itemCode: undefined }]),
    );
  });
});

describe("basketCartLine 記住籃子條碼", () => {
  it("掃販售籃時以籃子條碼當末三碼來源", () => {
    const line = basketCartLine({
      id: 3,
      code: "K1-000042",
      name: "營釘一籃",
      note: "有幾支彎掉",
      unit_price: "10",
      remaining_qty: 20,
      sources: [],
      brand_id: 9,
      category_id: null,
      cost_reference: { sample_count: 0, unit_cost_min: null, unit_cost_max: null },
      is_active: true,
      store_id: 1,
    });
    expect(lineNoteText(line)).toBe("有幾支彎掉-042");
    expect(line.brandId).toBe(9);
  });
});

describe("cartItemCount（總共幾件）", () => {
  it("把每一行的數量加總：一頂帳篷＋3 罐瓦斯＝4 件", () => {
    expect(
      cartItemCount([
        base,
        { key: "C:7", lineType: "CATALOG", description: "瓦斯罐", unitPrice: 120, qty: 3 },
      ]),
    ).toBe(4);
  });

  it("空車是 0", () => {
    expect(cartItemCount([])).toBe(0);
  });
});

describe("packCartLines（組合包袋裝條碼，ADR-028）", () => {
  const scan = (items: Partial<components["schemas"]["BundlePackScanItemRead"]>[]) => ({
    id: 1,
    code: "P1-ABCDEF0123",
    name: "濾掛 12 入袋",
    campaign_id: 9,
    campaign_name: "濾掛 12 入",
    bundle_price: "500",
    campaign_effective: true,
    items: items.map((item) => ({
      item_type: "CATALOG" as const,
      target_id: 7,
      qty: 6,
      code: "BIRD",
      name: "天堂鳥濾掛",
      unit_price: "50",
      note: null,
      brand_id: null,
      stock: 144,
      available: true,
      unavailable_reason: null,
      ...item,
    })),
  });

  it("袋裡每項轉成一行，鍵與單掃時相同（之後再掃同一件會合併）", () => {
    const lines = packCartLines(
      scan([
        { target_id: 7, code: "BIRD" },
        { target_id: 8, code: "PEACH", name: "蜜桃", note: "效期短", brand_id: 3 },
        { item_type: "SERIALIZED", target_id: 21, code: "S1-ABCDEF0123", qty: 1, stock: 1, name: "帳篷", unit_price: "3000" },
        { item_type: "BULK_BASKET", target_id: 5, code: "K1-ABCDEF0123", qty: 10, stock: 30, name: "營釘", unit_price: "20" },
      ]),
    );
    expect(lines.map((l) => [l.key, l.lineType, l.qty, l.unitPrice, l.maxQty])).toEqual([
      ["C:7", "CATALOG", 6, 50, 144],
      ["C:8", "CATALOG", 6, 50, 144],
      ["S:S1-ABCDEF0123", "SERIALIZED", 1, 3000, 1],
      ["K:5", "BULK_LOT", 10, 20, 30],
    ]);
    expect(lines[1]).toMatchObject({ catalogProductId: 8, barcode: "PEACH", note: "效期短", brandId: 3 });
    expect(lines[2]).toMatchObject({ itemCode: "S1-ABCDEF0123" });
    expect(lines[3]).toMatchObject({ bulkBasketId: 5, barcode: "K1-ABCDEF0123" });
  });

  it("袋裡有任何一件不能賣：整袋不加，說清楚是哪一件", () => {
    expect(() =>
      packCartLines(scan([{}, { name: "蜜桃", available: false, unavailable_reason: "庫存只剩 5" }])),
    ).toThrow("這袋不完整，不能照組合價賣：蜜桃（庫存只剩 5）");
  });
});
