// 純函式單元測試：溢價率夾擠、百分比格式化。
import { describe, expect, it } from "vitest";

import { clampRate, formatPct, parsePctInput, parseRateInput } from "@/features/settings/helpers";

describe("clampRate", () => {
  it("在範圍內的值不變", () => {
    expect(clampRate("0.1000", "0.0000", "0.2000")).toBe("0.1000");
  });
  it("低於 min 夾到 min", () => {
    expect(clampRate("-0.0100", "0.0000", "0.2000")).toBe("0.0000");
  });
  it("高於 max 夾到 max", () => {
    expect(clampRate("0.3000", "0.0000", "0.2000")).toBe("0.2000");
  });
  it("邊界值 min 不被夾", () => {
    expect(clampRate("0.0000", "0.0000", "0.2000")).toBe("0.0000");
  });
  it("邊界值 max 不被夾", () => {
    expect(clampRate("0.2000", "0.0000", "0.2000")).toBe("0.2000");
  });
});

describe("formatPct", () => {
  it("0.1000 → 10%", () => {
    expect(formatPct("0.1000")).toBe("10%");
  });
  it("0.0500 → 5%", () => {
    expect(formatPct("0.0500")).toBe("5%");
  });
  it("0.2000 → 20%", () => {
    expect(formatPct("0.2000")).toBe("20%");
  });
  it("0.1250 → 12.5%", () => {
    expect(formatPct("0.1250")).toBe("12.5%");
  });
  it("0.0000 → 0%", () => {
    expect(formatPct("0.0000")).toBe("0%");
  });
});

describe("parseRateInput", () => {
  it("合法百分比字串 → 小數字串", () => {
    expect(parseRateInput("10")).toBe("0.1000");
  });
  it("含小數的百分比", () => {
    expect(parseRateInput("12.5")).toBe("0.1250");
  });
  it("0 → 0.0000", () => {
    expect(parseRateInput("0")).toBe("0.0000");
  });
  it("空字串 → null", () => {
    expect(parseRateInput("")).toBeNull();
  });
  it("非數字 → null", () => {
    expect(parseRateInput("abc")).toBeNull();
  });
  it("負數 → null", () => {
    expect(parseRateInput("-5")).toBeNull();
  });
});

describe("parsePctInput", () => {
  it("合法整數 → number", () => {
    expect(parsePctInput("45")).toBe(45);
  });
  it("0 → 0", () => {
    expect(parsePctInput("0")).toBe(0);
  });
  it("99 → 99", () => {
    expect(parsePctInput("99")).toBe(99);
  });
  it("100 → null（超出 0-99）", () => {
    expect(parsePctInput("100")).toBeNull();
  });
  it("負數 → null", () => {
    expect(parsePctInput("-1")).toBeNull();
  });
  it("小數 → null", () => {
    expect(parsePctInput("45.5")).toBeNull();
  });
  it("空字串 → null", () => {
    expect(parsePctInput("")).toBeNull();
  });
});
