import { describe, expect, it } from "vitest";

import { taipeiDateForScript } from "@/scripts/_taipei-date.mjs";

describe("automation script Taiwan date", () => {
  it("uses the Taiwan date instead of UTC date", () => {
    expect(taipeiDateForScript(new Date("2026-07-21T16:30:00Z"))).toBe("2026-07-22");
  });
});
