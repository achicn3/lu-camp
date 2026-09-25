import { describe, expect, it } from "vitest";

import { estimateProgress } from "@/features/intake/progress";

describe("估價進度（只看件數）", () => {
  it("還差幾件、進度百分比", () => {
    expect(estimateProgress(3, 2)).toEqual({ pct: 67, missing: 1, over: 0 });
    expect(estimateProgress(3, 0)).toEqual({ pct: 0, missing: 3, over: 0 });
  });
  it("估齊與估多", () => {
    expect(estimateProgress(3, 3)).toEqual({ pct: 100, missing: 0, over: 0 });
    expect(estimateProgress(3, 4)).toEqual({ pct: 100, missing: 0, over: 1 });
  });
});
