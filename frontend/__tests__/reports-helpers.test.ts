// Unit tests for pure helpers in features/reports/reports.ts
import { describe, expect, it } from "vitest";

import {
  EFFECTIVENESS_LABELS,
  GRANULARITY_OPTIONS,
  defaultDateRange,
  exclusiveEnd,
  isoDate,
} from "@/features/reports/reports";

describe("isoDate", () => {
  it("formats a date as YYYY-MM-DD", () => {
    expect(isoDate(new Date(2026, 0, 5))).toBe("2026-01-05");
    expect(isoDate(new Date(2026, 11, 31))).toBe("2026-12-31");
  });
});

describe("defaultDateRange", () => {
  it("returns from=30 days ago, to=today", () => {
    const now = new Date(2026, 5, 18); // 2026-06-18
    const range = defaultDateRange(now);
    expect(range.to).toBe("2026-06-18");
    expect(range.from).toBe("2026-05-19");
  });

  it("handles month boundaries", () => {
    const now = new Date(2026, 0, 15); // 2026-01-15
    const range = defaultDateRange(now);
    expect(range.to).toBe("2026-01-15");
    expect(range.from).toBe("2025-12-16");
  });
});

describe("exclusiveEnd", () => {
  it("returns next-day 00:00:00 (half-open upper bound, includes whole end date)", () => {
    expect(exclusiveEnd("2026-06-18")).toBe("2026-06-19T00:00:00");
  });
  it("rolls over month/year boundaries", () => {
    expect(exclusiveEnd("2026-01-31")).toBe("2026-02-01T00:00:00");
    expect(exclusiveEnd("2026-12-31")).toBe("2027-01-01T00:00:00");
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
