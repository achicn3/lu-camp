import { describe, expect, it } from "vitest";

import { batchIdFromSlip, draftFrom, editFor, missingOf } from "@/features/intake/listing";
import type { components } from "@/lib/api-types";

type Item = components["schemas"]["IntakeItemRead"];

const chair: Item = {
  kind: "SERIALIZED",
  id: 7,
  code: "S1-ABC",
  name: "黑色折疊椅",
  consignment: false,
  grade: "B",
  brand_id: null,
  brand_name: null,
  product_model_id: null,
  product_model_name: null,
  category_id: null,
  category_name: null,
  listed_price: "500",
  qty: 1,
  acquisition_cost: "250",
  retail_price: "1000",
  note: null,
  listed: false,
  missing: ["分類", "品牌"],
};

describe("待整理上架的草稿", () => {
  it("沒改就不送", () => {
    expect(editFor(chair, draftFrom(chair))).toBeNull();
  });

  it("只送改過的欄位", () => {
    const draft = { ...draftFrom(chair), categoryId: 3, categoryName: "露營椅", price: "520" };
    expect(editFor(chair, draft)).toEqual({
      kind: "SERIALIZED",
      id: 7,
      category_id: 3,
      listed_price: "520",
    });
  });

  it("清掉品牌會連型號一起送 null", () => {
    const withBrand = { ...chair, brand_id: 2, brand_name: "Coleman", product_model_id: 9 };
    const draft = { ...draftFrom(withBrand), brandId: null, brandName: null, modelId: null };
    expect(editFor(withBrand, draft)).toEqual({
      kind: "SERIALIZED",
      id: 7,
      brand_id: null,
      product_model_id: null,
    });
  });

  it("散裝不送成色與型號", () => {
    const pegs: Item = { ...chair, kind: "BULK_LOT", id: 3, grade: null, qty: 10 };
    const draft = { ...draftFrom(pegs), grade: "A" as const, modelId: 5, categoryId: 1 };
    expect(editFor(pegs, draft)).toEqual({ kind: "BULK_LOT", id: 3, category_id: 1 });
  });

  it("備註空白＝清掉", () => {
    const noted = { ...chair, note: "缺袋子" };
    expect(editFor(noted, { ...draftFrom(noted), note: "  " })).toEqual({
      kind: "SERIALIZED",
      id: 7,
      note: null,
    });
  });

  it("缺什麼依草稿即時算", () => {
    expect(missingOf(draftFrom(chair))).toEqual(["分類", "品牌"]);
    expect(missingOf({ ...draftFrom(chair), categoryId: 1, brandId: 2 })).toEqual([]);
  });
});

describe("掃收件單條碼", () => {
  it("IN 開頭條碼或純數字都認得", () => {
    expect(batchIdFromSlip("IN000123")).toBe(123);
    expect(batchIdFromSlip(" in000045 ")).toBe(45);
    expect(batchIdFromSlip("88")).toBe(88);
  });
  it("看不懂回 null", () => {
    expect(batchIdFromSlip("A032")).toBeNull();
    expect(batchIdFromSlip("IN000000")).toBeNull();
    expect(batchIdFromSlip("")).toBeNull();
  });
});
