import { describe, expect, it } from "vitest";

import { formatNtd, parseNtd } from "@/lib/money";

describe("money (NT$ 整數元)", () => {
  it("formats with thousands separators", () => {
    expect(formatNtd(1234567)).toBe("1,234,567");
    expect(formatNtd(0)).toBe("0");
  });

  it("parses integer strings, stripping commas", () => {
    expect(parseNtd("1,234")).toBe(1234);
    expect(parseNtd(" 50 ")).toBe(50);
  });

  it("rejects non-integer input (no float)", () => {
    expect(parseNtd("12.5")).toBeNull();
    expect(parseNtd("abc")).toBeNull();
  });
});
