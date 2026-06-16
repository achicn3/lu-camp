import { describe, expect, it } from "vitest";

import {
  MEMBER_TABS,
  labelFor,
  ROLE_LABELS,
  rolesLabel,
  SOURCE_TYPE_LABELS,
} from "@/features/member/labels";

describe("member 顯示標籤", () => {
  it("rolesLabel 翻譯並以、串接、空陣列回 —", () => {
    expect(rolesLabel(["MEMBER", "SELLER"])).toBe("會員、賣方");
    expect(rolesLabel([])).toBe("—");
  });

  it("labelFor 查無時原樣回傳（不吞未知值）", () => {
    expect(labelFor(ROLE_LABELS, "MEMBER")).toBe("會員");
    expect(labelFor(SOURCE_TYPE_LABELS, "BUYOUT")).toBe("買斷");
    expect(labelFor(ROLE_LABELS, "UNKNOWN")).toBe("UNKNOWN");
  });

  it("分頁定義含五頁、含編輯頁", () => {
    expect(MEMBER_TABS.map((t) => t.key)).toEqual([
      "overview",
      "purchases",
      "consignments",
      "sourced",
      "edit",
    ]);
  });
});
