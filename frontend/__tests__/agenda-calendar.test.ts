// 行事曆的純計算（docs/99 §9）：月曆格、跨天事件展開、重複規則的白話說明、過期分組。
import { describe, expect, it } from "vitest";

import {
  type AgendaItem,
  addMonths,
  formatDayHeading,
  groupOverdue,
  itemTone,
  listDays,
  monthGrid,
  pyWeekday,
  recurrenceSummary,
  spreadByDay,
} from "@/features/agenda/calendar";

function item(partial: Partial<AgendaItem> & Pick<AgendaItem, "key" | "day">): AgendaItem {
  return {
    kind: "TASK",
    title: "待辦",
    end_day: null,
    start_time: null,
    end_time: null,
    note: null,
    status: "OPEN",
    overdue: false,
    manager_only: false,
    assignee_staff_id: null,
    assignee_name: null,
    lead_days: 3,
    task_id: null,
    series_id: null,
    occurrence_on: null,
    event_id: null,
    event_kind: null,
    campaign_id: null,
    campaign_status: null,
    href: null,
    ...partial,
  };
}

describe("月曆格", () => {
  it("2026 年 11 月：從週日開始、固定六週", () => {
    const grid = monthGrid(2026, 11);
    expect(grid.weeks).toHaveLength(6);
    expect(grid.weeks[0]).toEqual([
      "2026-11-01",
      "2026-11-02",
      "2026-11-03",
      "2026-11-04",
      "2026-11-05",
      "2026-11-06",
      "2026-11-07",
    ]);
    expect(grid.start).toBe("2026-11-01");
    expect(grid.end).toBe("2026-12-12");
  });

  it("月初不是週日時，前面補上個月的日子", () => {
    // 2027-02-01 是週一
    const grid = monthGrid(2027, 2);
    expect(grid.weeks[0][0]).toBe("2027-01-31");
    expect(grid.weeks[0][1]).toBe("2027-02-01");
  });

  it("跨年換月", () => {
    expect(addMonths(2026, 12, 1)).toEqual({ year: 2027, month: 1 });
    expect(addMonths(2027, 1, -1)).toEqual({ year: 2026, month: 12 });
    expect(addMonths(2026, 11, -13)).toEqual({ year: 2025, month: 10 });
  });

  it("日期標題與星期（後端星期 0＝週一）", () => {
    expect(formatDayHeading("2026-11-10")).toBe("11/10（二）");
    expect(pyWeekday("2026-11-09")).toBe(0);
    expect(pyWeekday("2026-11-15")).toBe(6);
    expect(listDays("2026-12-30", 3)).toEqual(["2026-12-30", "2026-12-31", "2027-01-01"]);
  });
});

describe("依日期分組", () => {
  it("跨天的事件每一天都要出現，但不超出查詢範圍", () => {
    const market = item({
      key: "event:1:2026-11-07",
      kind: "EVENT",
      day: "2026-11-07",
      end_day: "2026-11-09",
    });
    const task = item({ key: "task:2", day: "2026-11-08" });
    const byDay = spreadByDay([market, task], "2026-11-08", "2026-11-30");
    expect(byDay.get("2026-11-07")).toBeUndefined();
    expect(byDay.get("2026-11-08")?.map((i) => i.key)).toEqual(["event:1:2026-11-07", "task:2"]);
    expect(byDay.get("2026-11-09")?.map((i) => i.key)).toEqual(["event:1:2026-11-07"]);
    expect(byDay.get("2026-11-10")).toBeUndefined();
  });
});

describe("重複規則的白話說明", () => {
  it("四種規則", () => {
    expect(recurrenceSummary({ kind: "WEEKLY", weekdays: [3, 0] })).toBe("每週一、四");
    expect(recurrenceSummary({ kind: "MONTHLY", weekdays: [], day_of_month: 5 })).toBe(
      "每月 5 號",
    );
    expect(recurrenceSummary({ kind: "MONTHLY", weekdays: [], day_of_month: 31 })).toBe(
      "每月 31 號（沒有 31 號的月份是月底）",
    );
    expect(
      recurrenceSummary({
        kind: "EVERY_N_MONTHS",
        weekdays: [],
        day_of_month: 10,
        interval_months: 2,
      }),
    ).toBe("每 2 個月的 10 號");
    expect(
      recurrenceSummary({ kind: "YEARLY", weekdays: [], month_of_year: 3, day_of_month: 1 }),
    ).toBe("每年 3/1");
  });

  it("有結束日要講出來", () => {
    expect(
      recurrenceSummary({ kind: "WEEKLY", weekdays: [0], until: "2027-03-01" }),
    ).toBe("每週一，到 2027/03/01 為止");
  });
});

describe("顏色分類與過期分組", () => {
  it("待辦依狀態、事件依類型分色", () => {
    expect(itemTone(item({ key: "a", day: "2026-11-10" }))).toBe("task");
    expect(itemTone(item({ key: "b", day: "2026-11-01", overdue: true }))).toBe("overdue");
    expect(itemTone(item({ key: "c", day: "2026-11-01", status: "DONE" }))).toBe("done");
    expect(itemTone(item({ key: "d", day: "2026-11-01", status: "SKIPPED" }))).toBe("done");
    expect(
      itemTone(item({ key: "e", day: "2026-11-01", kind: "EVENT", event_kind: "CLOSED" })),
    ).toBe("closed");
    expect(
      itemTone(item({ key: "f", day: "2026-11-01", kind: "EVENT", event_kind: "MARKET" })),
    ).toBe("market");
    expect(itemTone(item({ key: "g", day: "2026-11-01", kind: "CAMPAIGN_START" }))).toBe(
      "campaign",
    );
  });

  it("同一個重複待辦過期好幾次，合成一組；單次待辦各自一組", () => {
    const groups = groupOverdue([
      item({ key: "occurrence:3:2026-11-02", day: "2026-11-02", series_id: 3, title: "叫牛奶" }),
      item({ key: "task:9", day: "2026-11-03", title: "回覆廠商" }),
      item({ key: "occurrence:3:2026-11-05", day: "2026-11-05", series_id: 3, title: "叫牛奶" }),
    ]);
    expect(groups.map((g) => [g.title, g.items.length, g.seriesId])).toEqual([
      ["叫牛奶", 2, 3],
      ["回覆廠商", 1, null],
    ]);
  });
});
