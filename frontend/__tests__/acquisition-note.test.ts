// 收購逐列備註（2026-09-04 裁示：一列一則，套用該列全部件數）。
//
// 驗機當下就記下「缺營釘」「右袖口磨損」最順手；錯過這個時機，店員得等收購完成後
// 回庫存頁逐件補。多件展開後是 N 件**各自獨立**的商品，備註跟著複製過去。
import { describe, expect, it } from "vitest";

import { expandByQty } from "@/features/acquisition/quantity";
import type { ItemDraft } from "@/features/acquisition/validation";

const row = (over: Partial<ItemDraft & { qty: string }> = {}) => ({
  name: "帳篷",
  grade: "A" as const,
  categoryId: 1,
  brandId: null,
  productModelId: null,
  listedPrice: "1000",
  acquisitionCost: "500",
  commissionPct: "",
  note: "",
  qty: "1",
  ...over,
});

describe("收購備註隨件數展開", () => {
  it("一列 3 件 → 三件都拿到同一則備註", () => {
    const out = expandByQty([row({ qty: "3", note: "缺營釘一支" })]);
    expect(out).toHaveLength(3);
    expect(out.map((r) => r.note)).toEqual(["缺營釘一支", "缺營釘一支", "缺營釘一支"]);
  });

  it("展開後各自獨立：改其中一件的備註不影響其他件", () => {
    const out = expandByQty([row({ qty: "2", note: "共用備註" })]);
    out[0].note = "只有這件有刮痕";
    expect(out[1].note).toBe("共用備註");
  });

  it("不同列各自帶自己的備註，不互相污染", () => {
    const out = expandByQty([
      row({ qty: "2", name: "帳篷", note: "缺營釘" }),
      row({ qty: "1", name: "爐頭", note: "附原廠盒" }),
    ]);
    expect(out.map((r) => `${r.name}:${r.note}`)).toEqual([
      "帳篷:缺營釘",
      "帳篷:缺營釘",
      "爐頭:附原廠盒",
    ]);
  });

  it("沒填備註的列展開後仍是沒填（不會冒出空白備註）", () => {
    const out = expandByQty([row({ qty: "2" })]);
    expect(out.every((r) => r.note === "")).toBe(true);
  });
});
