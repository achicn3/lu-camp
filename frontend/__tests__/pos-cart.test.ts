import { describe, expect, it } from "vitest";

import {
  type CartLine,
  addLine,
  cartTotal,
  lineTotal,
  removeLine,
  setQty,
  toSaleLines,
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
});
