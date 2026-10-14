import { describe, expect, it, vi } from "vitest";

import { batchIdFromSlip, draftFrom, editFor, missingOf, modelFromName } from "@/features/intake/listing";
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

  it("原價：帶出估價時填的；改了才送，清空＝送 null（店主 2026-10-09）", () => {
    expect(draftFrom(chair).retailPrice).toBe("1000");
    expect(editFor(chair, { ...draftFrom(chair), retailPrice: "1,280" })).toEqual({
      kind: "SERIALIZED",
      id: 7,
      retail_price: "1280",
    });
    expect(editFor(chair, { ...draftFrom(chair), retailPrice: " " })).toEqual({
      kind: "SERIALIZED",
      id: 7,
      retail_price: null,
    });
    const noRetail = { ...chair, retail_price: null };
    expect(draftFrom(noRetail).retailPrice).toBe("");
    expect(editFor(noRetail, draftFrom(noRetail))).toBeNull();
  });

  it("原價打不是數字：不送（畫面另外提示）", () => {
    expect(editFor(chair, { ...draftFrom(chair), retailPrice: "abc" })).toBeNull();
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

describe("型號旁的「品名」：用目前的品名當型號（店主 2026-10-15）", () => {
  it("同品牌已有同名型號就直接選它，不重複建立", async () => {
    const search = vi.fn().mockResolvedValue([
      { id: 3, name: "焚火台 M" },
      { id: 4, name: "焚火台 M Pro" },
    ]);
    const create = vi.fn();
    expect(await modelFromName(" 焚火台 m ", { search, create })).toEqual({ id: 3, name: "焚火台 M" });
    expect(search).toHaveBeenCalledWith("焚火台 m");
    expect(create).not.toHaveBeenCalled();
  });

  it("沒有同名的就用品名建立新型號", async () => {
    const search = vi.fn().mockResolvedValue([{ id: 4, name: "焚火台 M Pro" }]);
    const create = vi.fn().mockResolvedValue({ id: 9, name: "焚火台 M" });
    expect(await modelFromName("焚火台 M", { search, create })).toEqual({ id: 9, name: "焚火台 M" });
    expect(create).toHaveBeenCalledWith("焚火台 M");
  });

  it("品名是空的就不做", async () => {
    await expect(modelFromName("  ", { search: vi.fn(), create: vi.fn() })).rejects.toThrow("請先填品名");
  });
});
