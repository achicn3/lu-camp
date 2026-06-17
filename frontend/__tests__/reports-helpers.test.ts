// Unit tests for pure helpers in features/reports/reports.ts
import { describe, expect, it } from "vitest";

import {
  EFFECTIVENESS_LABELS,
  GRANULARITY_OPTIONS,
  defaultDateRange,
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
