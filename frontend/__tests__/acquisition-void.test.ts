import { describe, expect, it } from "vitest";

import { canVoid, voidBlockReason, voidErrorMessage } from "@/features/acquisition/void";

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

describe("voidBlockReason", () => {
  it("已作廢回對應提示", () => {
    expect(voidBlockReason({ voided_at: "2026-06-19T00:00:00Z", type: "BUYOUT" })).toMatch(/已作廢/);
  });
  it("寄售回對應提示", () => {
    expect(voidBlockReason({ voided_at: null, type: "CONSIGNMENT" })).toMatch(/寄售/);
  });
  it("可作廢回 null", () => {
    expect(voidBlockReason({ voided_at: null, type: "BUYOUT" })).toBeNull();
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
