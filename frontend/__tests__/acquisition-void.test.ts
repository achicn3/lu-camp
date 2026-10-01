import { describe, expect, it } from "vitest";

import { canVoid, recordVoidHint, recordVoidMode, voidErrorMessage } from "@/features/acquisition/void";

describe("canVoid", () => {
  it("買斷/散裝未作廢 → 可作廢", () => {
    expect(canVoid({ voided_at: null, type: "BUYOUT" })).toBe(true);
    expect(canVoid({ voided_at: null, type: "BULK_LOT" })).toBe(true);
  });
  it("已作廢 → 不可作廢", () => {
    expect(canVoid({ voided_at: "2026-06-19T00:00:00Z", type: "BUYOUT" })).toBe(false);
  });
  it("寄售 → 不可作廢", () => {
    expect(canVoid({ voided_at: null, type: "CONSIGNMENT" })).toBe(false);
  });
});

describe("voidErrorMessage", () => {
  it("優先採用後端 detail（已是分案 zh-TW）", () => {
    expect(voidErrorMessage(409, "收購含已售出的庫存，不可作廢")).toBe("收購含已售出的庫存，不可作廢");
  });
  it("detail 缺漏 → 依 HTTP status 退回預設", () => {
    expect(voidErrorMessage(403, null)).toMatch(/管理者/);
    expect(voidErrorMessage(404, null)).toMatch(/找不到/);
    expect(voidErrorMessage(409, "")).toMatch(/不可作廢/);
    expect(voidErrorMessage(422, "   ")).toMatch(/作廢/);
  });
  it("未知 status → 通用失敗訊息", () => {
    expect(voidErrorMessage(500, null)).toMatch(/失敗/);
  });
});

describe("收購紀錄的作廢鈕（一顆鈕，依類型與擋下原因決定行為）", () => {
  type Mode = ReturnType<typeof recordVoidMode>;
  const cases: [string, string | null, string | null, Mode][] = [
    // [類型, void_block, voided_at, 預期模式]
    ["BUYOUT", null, null, "SELECT_ALL"],
    ["BUYOUT", "HAS_SOLD_ITEMS", null, "SELECT_ALL"], // 已售的勾不了，其餘預設全勾
    ["BUYOUT", "CREDIT_SPENT", null, "SELECT_SOME"], // 整張沖不回，只作廢幾件可能可以
    ["BUYOUT", "NO_OPEN_CASH_SESSION", null, null],
    ["BUYOUT", "ALREADY_VOIDED", "2026-10-01T00:00:00Z", null],
    ["BULK_LOT", null, null, "WHOLE"],
    ["BULK_LOT", "HAS_SOLD_ITEMS", null, null],
    ["BULK_LOT", "NO_OPEN_CASH_SESSION", null, null],
    ["CONSIGNMENT", "CONSIGNMENT", null, null],
  ];
  it.each(cases)("%s／%s／voided=%s → %s", (type, block, voidedAt, mode) => {
    const row = { type, void_block: block, voided_at: voidedAt } as Parameters<typeof recordVoidMode>[0];
    expect(recordVoidMode(row)).toBe(mode);
  });

  it("說明文字：買斷單講清楚還能怎麼作廢；已作廢不重複講", () => {
    const hint = (type: string, block: string | null) =>
      recordVoidHint({ type, void_block: block, voided_at: null } as Parameters<typeof recordVoidHint>[0]);
    // 逐件作廢過的件也算「已動用」，所以不能說成只有賣出
    expect(hint("BUYOUT", "HAS_SOLD_ITEMS")).toBe("部分商品已賣出或已作廢，其餘可勾選作廢");
    expect(hint("BUYOUT", "CREDIT_SPENT")).toContain("購物金已被用掉");
    expect(hint("BULK_LOT", "HAS_SOLD_ITEMS")).toBe("已有商品賣出或報廢，不能作廢");
    expect(hint("BUYOUT", "NO_OPEN_CASH_SESSION")).toBe("要退回現金，請先開帳");
    expect(hint("BUYOUT", "ALREADY_VOIDED")).toBeNull();
    expect(hint("BUYOUT", null)).toBeNull();
  });
});
