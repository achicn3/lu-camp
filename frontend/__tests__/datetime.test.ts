import { describe, expect, it } from "vitest";

import {
  exclusiveEndOfTaipeiDay,
  formatTaipeiDate,
  formatTaipeiDateTime,
  formatTaipeiTime,
  shiftIsoDate,
  startOfTaipeiDay,
  taipeiDate,
  taipeiDateTimeLocalToUtc,
} from "@/lib/datetime";

describe("Taipei display formatting", () => {
  const instant = "2026-07-21T16:30:45Z";

  it("formats the same instant with the Taiwan calendar date", () => {
    expect(formatTaipeiDateTime(instant)).toBe("2026/07/22 00:30");
    expect(formatTaipeiDate(instant)).toBe("2026/07/22");
    expect(formatTaipeiTime(instant)).toBe("00:30");
  });

  it("returns a dash for missing or invalid timestamps", () => {
    expect(formatTaipeiDateTime(null)).toBe("—");
    expect(formatTaipeiDateTime("not-a-date")).toBe("—");
  });

  it("rejects timestamps without an explicit timezone offset", () => {
    expect(formatTaipeiDateTime("2026-07-22T00:30:45")).toBe("—");
  });
});

describe("Taipei business-date boundaries", () => {
  it("gets today in Taiwan even when UTC is still the previous date", () => {
    expect(taipeiDate(new Date("2026-07-21T16:30:00Z"))).toBe("2026-07-22");
  });

  it("converts a Taiwan date to explicit UTC half-open bounds", () => {
    expect(startOfTaipeiDay("2026-07-22")).toBe("2026-07-21T16:00:00.000Z");
    expect(exclusiveEndOfTaipeiDay("2026-07-22")).toBe("2026-07-22T16:00:00.000Z");
  });

  it("shifts calendar dates across month and leap-year boundaries", () => {
    expect(shiftIsoDate("2026-03-01", -1)).toBe("2026-02-28");
    expect(shiftIsoDate("2024-03-01", -1)).toBe("2024-02-29");
    expect(shiftIsoDate("2026-12-31", 1)).toBe("2027-01-01");
  });
});

describe("Taipei datetime-local input", () => {
  it("interprets the wall-clock value as Asia/Taipei, independent of browser timezone", () => {
    expect(taipeiDateTimeLocalToUtc("2026-07-22T00:30")).toBe(
      "2026-07-21T16:30:00.000Z",
    );
  });

  it("rejects malformed or impossible values", () => {
    expect(() => taipeiDateTimeLocalToUtc("2026-02-30T10:00")).toThrow();
    expect(() => taipeiDateTimeLocalToUtc("hello")).toThrow();
  });
});
