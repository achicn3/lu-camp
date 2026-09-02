// 商品備註純邏輯：長度上限、列表摘要、購物車提醒挑選（2026-09-02 裁示）。
import { describe, expect, it } from "vitest";

import { NOTE_MAX_LENGTH, hasNote, noteSummary } from "@/features/inventory/inventory";
import type { CartLine } from "@/features/pos/cart";
import { linesWithNotes, noteAckFingerprint } from "@/features/pos/cart";

describe("備註上限", () => {
  it("與後端 String(500) 一致，避免前端放行卻被 422 退回", () => {
    expect(NOTE_MAX_LENGTH).toBe(500);
  });
});

describe("hasNote", () => {
  it("null / undefined / 空白一律視為沒有備註", () => {
    expect(hasNote(null)).toBe(false);
    expect(hasNote(undefined)).toBe(false);
    expect(hasNote("")).toBe(false);
    expect(hasNote("   ")).toBe(false);
  });

  it("有實質內容才算有備註", () => {
    expect(hasNote("缺充電線")).toBe(true);
  });
});

describe("noteSummary", () => {
  it("短備註原樣顯示", () => {
    expect(noteSummary("缺充電線", 10)).toBe("缺充電線");
  });

  it("過長截斷並加省略號，避免撐爆列表欄寬", () => {
    expect(noteSummary("一二三四五六七八九十", 5)).toBe("一二三四五…");
  });

  it("換行折成單行（列表只有一行高度）", () => {
    expect(noteSummary("第一行\n第二行", 20)).toBe("第一行 第二行");
  });

  it("沒有備註回空字串", () => {
    expect(noteSummary(null, 10)).toBe("");
    expect(noteSummary("   ", 10)).toBe("");
  });
});

describe("linesWithNotes（結帳提醒）", () => {
  const base: CartLine = {
    key: "S:S1-A",
    lineType: "SERIALIZED",
    description: "外套",
    unitPrice: 1000,
    qty: 1,
  };

  it("只挑出帶備註的行，順序照購物車", () => {
    const lines: CartLine[] = [
      { ...base, key: "a", description: "無備註品" },
      { ...base, key: "b", description: "帳篷", note: "缺營釘" },
      { ...base, key: "c", description: "外套", note: "  " },
      { ...base, key: "d", description: "爐頭", note: "附原廠盒" },
    ];
    expect(linesWithNotes(lines)).toEqual([
      { key: "b", description: "帳篷", note: "缺營釘" },
      { key: "d", description: "爐頭", note: "附原廠盒" },
    ]);
  });

  it("全都沒備註時回空陣列（結帳不該跳空對話框）", () => {
    expect(linesWithNotes([base, { ...base, key: "x" }])).toEqual([]);
  });

  it("備註前後空白修剪後才顯示", () => {
    const lines: CartLine[] = [{ ...base, note: "  缺充電線  " }];
    expect(linesWithNotes(lines)).toEqual([
      { key: "S:S1-A", description: "外套", note: "缺充電線" },
    ]);
  });
});

describe("noteAckFingerprint（提醒確認的有效範圍）", () => {
  const base: CartLine = {
    key: "a",
    lineType: "SERIALIZED",
    description: "外套",
    unitPrice: 1000,
    qty: 1,
  };

  it("沒有備註時為空字串（不需要確認）", () => {
    expect(noteAckFingerprint([base])).toBe("");
  });

  it("同一組備註穩定不變：確認過就不再重複打擾", () => {
    const lines: CartLine[] = [
      { ...base, key: "a", note: "缺充電線" },
      { ...base, key: "b", note: "有刮痕" },
    ];
    expect(noteAckFingerprint(lines)).toBe(noteAckFingerprint([...lines]));
  });

  it("再掃進一件有備註的商品 → 指紋改變，必須重新確認", () => {
    const before: CartLine[] = [{ ...base, key: "a", note: "缺充電線" }];
    const after: CartLine[] = [...before, { ...base, key: "b", note: "有刮痕" }];
    expect(noteAckFingerprint(after)).not.toBe(noteAckFingerprint(before));
  });

  it("移除有備註的商品也會改變指紋（確認的前提已不同）", () => {
    const before: CartLine[] = [
      { ...base, key: "a", note: "缺充電線" },
      { ...base, key: "b", note: "有刮痕" },
    ];
    const after: CartLine[] = [{ ...base, key: "a", note: "缺充電線" }];
    expect(noteAckFingerprint(after)).not.toBe(noteAckFingerprint(before));
  });

  it("只改數量不影響指紋（備註內容沒變，不必再確認一次）", () => {
    const before: CartLine[] = [{ ...base, key: "a", note: "缺充電線", qty: 1 }];
    const after: CartLine[] = [{ ...base, key: "a", note: "缺充電線", qty: 3 }];
    expect(noteAckFingerprint(after)).toBe(noteAckFingerprint(before));
  });
});
