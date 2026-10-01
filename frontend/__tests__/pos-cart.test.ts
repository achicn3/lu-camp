import { describe, expect, it } from "vitest";

import {
  type CartLine,
  addLine,
  cartTotal,
  lineTotal,
  removeLine,
  setQty,
  toSaleLines,
  togglePromoFree,
} from "@/features/pos/cart";

const serialized = (code: string, price: number): CartLine => ({
  key: `S:${code}`,
  lineType: "SERIALIZED",
  description: "雙人帳篷",
  unitPrice: price,
  qty: 1,
  itemCode: code,
  maxQty: 1,
});

const bulk = (id: number, price: number, remaining: number): CartLine => ({
  key: `B:${id}`,
  lineType: "BULK_LOT",
  description: "營釘散裝",
  unitPrice: price,
  qty: 1,
  bulkLotId: id,
  maxQty: remaining,
});

describe("cart 純邏輯", () => {
  it("行小計與總計以整數元相加", () => {
    const lines = [serialized("C1", 1800), bulk(7, 50, 100)];
    expect(lineTotal(lines[1])).toBe(50);
    expect(cartTotal(lines)).toBe(1850);
  });

  it("序號品重複加入被擋、回報 duplicate", () => {
    const first = addLine([], serialized("C1", 1800));
    const second = addLine(first.lines, serialized("C1", 1800));
    expect(second.lines).toHaveLength(1);
    expect(second.duplicateSerialized).toBe(true);
  });

  it("散裝同堆再加合併數量，且不超過 remaining 上限", () => {
    let lines = addLine([], bulk(7, 50, 3)).lines;
    lines = addLine(lines, { ...bulk(7, 50, 3), qty: 2 }).lines;
    expect(lines[0].qty).toBe(3); // 1+2=3
    lines = addLine(lines, { ...bulk(7, 50, 3), qty: 5 }).lines;
    expect(lines[0].qty).toBe(3); // clamp 到 remaining=3
  });

  // 店主 2026-10-01：庫存只剩 1 件時重複掃描，數量停在上限卻沒任何提示，店員會以為沒掃到。
  it("合併數量撞到庫存上限 → 回報 cappedAt（沒撞到就是 null）", () => {
    const first = addLine([], bulk(7, 50, 2));
    expect(first.cappedAt).toBeNull();
    const second = addLine(first.lines, bulk(7, 50, 2));
    expect(second.lines[0].qty).toBe(2);
    expect(second.cappedAt).toBeNull(); // 剛好加到 2，沒有被截
    const third = addLine(second.lines, bulk(7, 50, 2));
    expect(third.lines[0].qty).toBe(2);
    expect(third.cappedAt).toBe(2);
  });

  it("序號品重複不算撞上限（另有 duplicateSerialized 提示）", () => {
    const first = addLine([], serialized("C1", 1800));
    expect(addLine(first.lines, serialized("C1", 1800)).cappedAt).toBeNull();
  });

  it("setQty 夾在 [1, maxQty]；removeLine 移除", () => {
    const lines = addLine([], bulk(7, 50, 4)).lines;
    expect(setQty(lines, "B:7", 0)[0].qty).toBe(1);
    expect(setQty(lines, "B:7", 99)[0].qty).toBe(4);
    expect(removeLine(lines, "B:7")).toHaveLength(0);
  });

  it("toSaleLines 依 line_type 帶對應參照", () => {
    const payload = toSaleLines([serialized("C1", 1800), bulk(7, 50, 100)]);
    expect(payload[0]).toMatchObject({
      line_type: "SERIALIZED",
      item_code: "C1",
      qty: 1,
    });
    expect(payload[1]).toMatchObject({
      line_type: "BULK_LOT",
      bulk_lot_id: 7,
      qty: 1,
    });
  });

  it("「送這件」只在勾選時送出 promo_free（沒勾時 payload 與舊版相同）", () => {
    const lines = [serialized("C1", 1800), serialized("C2", 600)];
    expect("promo_free" in toSaleLines(lines)[0]).toBe(false);
    const chosen = togglePromoFree(lines, "S:C2");
    expect(chosen[1].promoFree).toBe(true);
    expect(toSaleLines(chosen)[1]).toMatchObject({ item_code: "C2", promo_free: true });
    expect("promo_free" in toSaleLines(chosen)[0]).toBe(false);
    expect(togglePromoFree(chosen, "S:C2")[1].promoFree).toBe(false);
  });
});
