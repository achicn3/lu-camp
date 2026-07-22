// Unit tests for pure helpers in features/reports/reports.ts
import { describe, expect, it } from "vitest";

import {
  EFFECTIVENESS_LABELS,
  FINANCIAL_GRANULARITY_OPTIONS,
  GRANULARITY_OPTIONS,
  computeChartScaling,
  defaultDateRange,
  exclusiveEnd,
  isoDate,
  startOfDay,
} from "@/features/reports/reports";

describe("isoDate", () => {
  it("formats an instant as the Taiwan YYYY-MM-DD", () => {
    expect(isoDate(new Date("2026-01-04T16:30:00Z"))).toBe("2026-01-05");
    expect(isoDate(new Date("2026-12-30T16:30:00Z"))).toBe("2026-12-31");
  });
});

describe("defaultDateRange", () => {
  it("returns from=30 days ago, to=today", () => {
    const now = new Date("2026-06-18T04:00:00Z");
    const range = defaultDateRange(now);
    expect(range.to).toBe("2026-06-18");
    expect(range.from).toBe("2026-05-19");
  });

  it("handles month boundaries", () => {
    const now = new Date("2026-01-15T04:00:00Z");
    const range = defaultDateRange(now);
    expect(range.to).toBe("2026-01-15");
    expect(range.from).toBe("2025-12-16");
  });
});

describe("date bounds (timezone-aware)", () => {
  it("startOfDay / exclusiveEnd return timezone-aware UTC instants (end with Z)", () => {
    expect(startOfDay("2026-06-18").endsWith("Z")).toBe(true);
    expect(exclusiveEnd("2026-06-18").endsWith("Z")).toBe(true);
  });
  it("exclusiveEnd is exactly 24h after startOfDay (covers whole local day, half-open)", () => {
    const start = new Date(startOfDay("2026-06-18")).getTime();
    const end = new Date(exclusiveEnd("2026-06-18")).getTime();
    expect(end - start).toBe(24 * 60 * 60 * 1000);
  });
  it("both equal Taiwan midnight regardless of the browser timezone", () => {
    expect(startOfDay("2026-06-18")).toBe("2026-06-17T16:00:00.000Z");
    expect(exclusiveEnd("2026-06-18")).toBe("2026-06-18T16:00:00.000Z");
  });
});

describe("EFFECTIVENESS_LABELS", () => {
  it("has all 7 metrics", () => {
    expect(Object.keys(EFFECTIVENESS_LABELS)).toHaveLength(7);
    expect(EFFECTIVENESS_LABELS.take_rate).toBe("選用率");
    expect(EFFECTIVENESS_LABELS.delta_per_1000).toBe("每千元損益 (delta)");
  });
});

describe("GRANULARITY_OPTIONS", () => {
  it("has day/week/month", () => {
    expect(GRANULARITY_OPTIONS.map((o) => o.value)).toEqual(["day", "week", "month"]);
  });
});

describe("FINANCIAL_GRANULARITY_OPTIONS", () => {
  it("has day/week/month/quarter with zh-TW labels", () => {
    expect(FINANCIAL_GRANULARITY_OPTIONS.map((o) => o.value)).toEqual(["day", "week", "month", "quarter"]);
    expect(FINANCIAL_GRANULARITY_OPTIONS[3].label).toBe("季");
  });
});

describe("computeChartScaling", () => {
  it("computes min/max/step for positive values", () => {
    const result = computeChartScaling([10, 50, 30, 80]);
    expect(result.min).toBe(0);
    expect(result.max).toBeGreaterThanOrEqual(80);
    expect(result.step).toBeGreaterThan(0);
    // ticks should cover from min to max
    expect(result.ticks.length).toBeGreaterThanOrEqual(2);
    expect(result.ticks[0]).toBe(0);
    expect(result.ticks[result.ticks.length - 1]).toBe(result.max);
  });

  it("handles all zeros", () => {
    const result = computeChartScaling([0, 0, 0]);
    expect(result.min).toBe(0);
    expect(result.max).toBeGreaterThan(0);
    expect(result.step).toBeGreaterThan(0);
  });

  it("handles negative values", () => {
    const result = computeChartScaling([-20, 50, -10, 30]);
    expect(result.min).toBeLessThanOrEqual(-20);
    expect(result.max).toBeGreaterThanOrEqual(50);
  });

  it("handles empty array", () => {
    const result = computeChartScaling([]);
    expect(result.min).toBe(0);
    expect(result.max).toBeGreaterThan(0);
    expect(result.ticks.length).toBeGreaterThanOrEqual(2);
  });

  it("handles single value", () => {
    const result = computeChartScaling([100]);
    expect(result.min).toBeLessThanOrEqual(0);
    expect(result.max).toBeGreaterThanOrEqual(100);
  });
});

import { formatRate } from "@/features/reports/reports";

describe("formatRate", () => {
  it("小數字串→百分比一位小數", () => {
    expect(formatRate("0.5807")).toBe("58.1%");
    expect(formatRate("0")).toBe("0.0%");
    expect(formatRate(0.5)).toBe("50.0%");
  });
  it("null/空/非數字→N/A", () => {
    expect(formatRate(null)).toBe("N/A");
    expect(formatRate(undefined)).toBe("N/A");
    expect(formatRate("")).toBe("N/A");
    expect(formatRate("abc")).toBe("N/A");
  });
});
