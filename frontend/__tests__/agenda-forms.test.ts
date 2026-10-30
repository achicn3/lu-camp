// 行事曆表單的純函式：重複規則草稿 ↔ API 欄位、數字欄位檢查（docs/99 §4.2）。
import { describe, expect, it } from "vitest";

import { buildRule, emptyRuleDraft, parseLeadDays, ruleDraftFrom } from "@/features/agenda/forms";

describe("重複規則草稿", () => {
  it("不重複＝沒有規則", () => {
    expect(buildRule(emptyRuleDraft())).toEqual({ rule: null, error: null });
  });

  it("每週：至少選一天，送出時排序", () => {
    expect(buildRule({ ...emptyRuleDraft(), kind: "WEEKLY" }).error).toBe("每週重複要選星期幾");
    expect(buildRule({ ...emptyRuleDraft(), kind: "WEEKLY", weekdays: [3, 0] })).toEqual({
      rule: { kind: "WEEKLY", weekdays: [0, 3], until: null },
      error: null,
    });
  });

  it("每月：幾號要 1–31", () => {
    expect(buildRule({ ...emptyRuleDraft(), kind: "MONTHLY", dayOfMonth: "32" }).error).toBe(
      "幾號要填 1 到 31",
    );
    expect(
      buildRule({ ...emptyRuleDraft(), kind: "MONTHLY", dayOfMonth: "5", until: "2027-06-30" }),
    ).toEqual({
      rule: { kind: "MONTHLY", weekdays: [], day_of_month: 5, until: "2027-06-30" },
      error: null,
    });
  });

  it("每幾個月：間隔 2–24", () => {
    const draft = { ...emptyRuleDraft(), kind: "EVERY_N_MONTHS" as const, dayOfMonth: "10" };
    expect(buildRule({ ...draft, intervalMonths: "1" }).error).toBe(
      "每幾個月要填 2 到 24（每個月請選「每月」）",
    );
    expect(buildRule({ ...draft, intervalMonths: "2" }).rule).toEqual({
      kind: "EVERY_N_MONTHS",
      weekdays: [],
      day_of_month: 10,
      interval_months: 2,
      until: null,
    });
  });

  it("每年：不存在的日期擋下，2/29 可以", () => {
    const draft = { ...emptyRuleDraft(), kind: "YEARLY" as const };
    expect(buildRule({ ...draft, monthOfYear: "2", dayOfMonth: "30" }).error).toBe(
      "2 月沒有 30 號",
    );
    expect(buildRule({ ...draft, monthOfYear: "2", dayOfMonth: "29" }).rule).toEqual({
      kind: "YEARLY",
      weekdays: [],
      month_of_year: 2,
      day_of_month: 29,
      until: null,
    });
  });

  it("從 API 欄位帶回草稿（修改時）", () => {
    expect(
      ruleDraftFrom({ kind: "EVERY_N_MONTHS", weekdays: [], day_of_month: 10, interval_months: 3 }),
    ).toEqual({
      kind: "EVERY_N_MONTHS",
      weekdays: [],
      dayOfMonth: "10",
      intervalMonths: "3",
      monthOfYear: "1",
      until: "",
    });
    expect(ruleDraftFrom(null).kind).toBe("NONE");
  });
});

describe("提前幾天提醒", () => {
  it("0–365 的整數，空白當 0", () => {
    expect(parseLeadDays("3")).toBe(3);
    expect(parseLeadDays("")).toBe(0);
    expect(parseLeadDays("366")).toBeNull();
    expect(parseLeadDays("1.5")).toBeNull();
    expect(parseLeadDays("-1")).toBeNull();
  });
});
