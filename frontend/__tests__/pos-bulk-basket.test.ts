// @vitest-environment jsdom
// 散裝販售籃（ADR-025）在 POS 的純邏輯：一籃一行、送出 bulk_basket_id、重整後還原得回來。
import { afterEach, describe, expect, it, vi } from "vitest";

import { restoreLines } from "@/features/customer-display/PosCustomerDisplay";
import { basketCartLine, toSaleLines } from "@/features/pos/cart";
import { withFreshNotes } from "@/features/pos/restoreNotes";
import type { components } from "@/lib/api-types";
import { setToken } from "@/lib/token";

type Basket = components["schemas"]["BulkBasketRead"];

const BASKET: Basket = {
  id: 5,
  store_id: 1,
  code: "K1-ABCDEF0123",
  name: "無品牌營釘",
  brand_id: null,
  category_id: null,
  unit_price: "20",
  note: "長短混裝",
  is_active: true,
  remaining_qty: 30,
  sources: [],
  cost_reference: { sample_count: 2, unit_cost_min: "5", unit_cost_max: "8" },
};

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

afterEach(() => vi.unstubAllGlobals());

describe("散裝販售籃購物車行", () => {
  it("一籃一行：可售上限是整籃剩餘，不是單一來源", () => {
    const line = basketCartLine(BASKET);
    expect(line).toMatchObject({
      key: "K:5",
      lineType: "BULK_LOT",
      description: "無品牌營釘",
      unitPrice: 20,
      qty: 1,
      bulkBasketId: 5,
      maxQty: 30,
      note: "長短混裝",
    });
    expect(line.bulkLotId).toBeUndefined();
  });

  it("籃內還有貨的那幾批，收購時寫的備註也要一起提醒；賣完的那批不提（Codex 第二輪）", () => {
    const source = (id: number, remaining: number, note: string | null) => ({
      bulk_lot_id: id,
      lot_code: `L1-${id}`,
      intake_date: "2026-09-20T02:00:00Z",
      total_qty: 10,
      remaining_qty: remaining,
      status: remaining > 0 ? ("ON_SALE" as const) : ("SOLD_OUT" as const),
      acquisition_cost: "50",
      unit_cost: "5",
      note,
    });
    const line = basketCartLine({
      ...BASKET,
      sources: [source(1, 0, "早就賣完的那批"), source(2, 18, "有 3 支彎掉"), source(3, 12, null)],
    });
    expect(line.note).toBe("長短混裝；有 3 支彎掉");
  });

  it("籃子和各批都沒備註時不提醒", () => {
    expect(basketCartLine({ ...BASKET, note: null }).note).toBeNull();
  });

  it("整籃賣完要擋下，不能加進購物車", () => {
    expect(() => basketCartLine({ ...BASKET, remaining_qty: 0 })).toThrow(/已售罄/);
  });

  it("結帳送 bulk_basket_id，不帶 bulk_lot_id（後端才會跨來源分配）", () => {
    const [line] = toSaleLines([{ ...basketCartLine(BASKET), qty: 12 }]);
    expect(line).toMatchObject({
      line_type: "BULK_LOT",
      bulk_basket_id: 5,
      bulk_lot_id: null,
      qty: 12,
    });
  });

  it("重整後從客顯快照還原成同一籃（鍵與識別都要對得上）", () => {
    const [line] = restoreLines([
      {
        item_key: "BULK_BASKET:5",
        line_type: "BULK_LOT",
        name: "無品牌營釘",
        qty: 3,
        unit_price: "20",
        original_unit_price: null,
        discount_amount: "0",
        line_total: "60",
        line_kind: "NORMAL",
        manual_discount_amount: "0",
        net_amount: "60",
      },
    ]);
    expect(line).toMatchObject({ key: "K:5", bulkBasketId: 5, qty: 3 });
    expect(line.bulkLotId).toBeUndefined();
  });

  it("還原時向籃子取最新備註", async () => {
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    const requested: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        requested.push(url);
        return new Response(JSON.stringify(BASKET), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const [line] = await withFreshNotes([{ ...basketCartLine(BASKET), note: undefined }]);
    expect(requested.some((u) => u.endsWith("/api/v1/bulk-baskets/5"))).toBe(true);
    expect(line.note).toBe("長短混裝");
  });
});
