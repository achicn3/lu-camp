// 純函式單元測試：桌號清單編輯（docs/35）。
import { describe, expect, it } from "vitest";

import {
  MAX_TABLES,
  addTable,
  buildMinimums,
  minimumDrafts,
  removeTable,
  sameMinimums,
  sameTables,
} from "@/features/settings/dineInTables";

describe("addTable", () => {
  it("去頭尾空白後加入，順序保持在最後", () => {
    expect(addTable(["A1"], "  A2 ")).toEqual({ tables: ["A1", "A2"], error: null });
  });

  it("空白桌號不可加入", () => {
    const r = addTable([], "   ");
    expect(r.tables).toEqual([]);
    expect(r.error).toContain("請輸入");
  });

  it("重複桌號擋下——POS 會排出兩顆一樣的按鈕，店員分不出、事後也查不出點了哪顆", () => {
    const r = addTable(["A1"], " A1 ");
    expect(r.tables).toEqual(["A1"]);
    expect(r.error).toContain("已存在");
  });

  it("超長桌號擋下（對齊 sales.table_no 欄寬）", () => {
    expect(addTable([], "A".repeat(21)).error).not.toBeNull();
    expect(addTable([], "A".repeat(20)).error).toBeNull();
  });

  it("超量擋下", () => {
    const full = Array.from({ length: MAX_TABLES }, (_, i) => `T${i}`);
    expect(addTable(full, "NEW").error).toContain(String(MAX_TABLES));
  });
});

describe("removeTable / sameTables", () => {
  it("移除指定桌號", () => {
    expect(removeTable(["A1", "A2"], "A1")).toEqual(["A2"]);
  });

  it("換序也算變更——順序即 POS 按鈕順序", () => {
    expect(sameTables(["A1", "A2"], ["A2", "A1"])).toBe(false);
    expect(sameTables(["A1", "A2"], ["A1", "A2"])).toBe(true);
  });
});

describe("桌號低消（2026-10-14 店主）", () => {
  it("從設定讀回：沒設的桌號為「不設低消」", () => {
    expect(
      minimumDrafts(["A1", "A2", "A3"], {
        A1: { mode: "amount", amount: "300" },
        A2: { mode: "per_person_item" },
      }),
    ).toEqual({
      A1: { mode: "amount", amount: "300" },
      A2: { mode: "per_person_item", amount: "" },
      A3: { mode: "none", amount: "" },
    });
  });

  it("組成送出內容：不設低消的桌號不送、已移除的桌號不送", () => {
    const drafts = {
      A1: { mode: "amount", amount: " 300 " },
      A2: { mode: "per_person_item", amount: "" },
      A3: { mode: "none", amount: "" },
      Z9: { mode: "amount", amount: "500" },
    } as const;
    expect(buildMinimums(["A1", "A2", "A3"], drafts)).toEqual({
      minimums: { A1: { mode: "amount", amount: "300" }, A2: { mode: "per_person_item" } },
      error: null,
    });
  });

  it.each(["", "0", "-5", "1.5", "abc", "10000000"])("最低金額「%s」擋下並說明是哪一桌", (amount) => {
    const r = buildMinimums(["A1"], { A1: { mode: "amount", amount } });
    expect(r.error).toContain("A1");
  });

  it("比對是否有變更：金額字串格式不同但數值相同不算變更", () => {
    expect(sameMinimums({ A1: { mode: "amount", amount: "300" } }, { A1: { mode: "amount", amount: "300" } })).toBe(true);
    expect(sameMinimums({ A1: { mode: "per_person_item" } }, {})).toBe(false);
    expect(sameMinimums({}, undefined)).toBe(true);
  });
});
