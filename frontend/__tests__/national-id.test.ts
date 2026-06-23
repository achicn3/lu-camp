import { describe, expect, it } from "vitest";

import { isValidNationalId } from "@/features/member/national-id";

describe("isValidNationalId（身分證字號檢核）", () => {
  it("合法碼", () => {
    expect(isValidNationalId("A123456789")).toBe(true);
    expect(isValidNationalId("B123456780")).toBe(true);
    expect(isValidNationalId("A223456781")).toBe(true);
  });

  it("末碼錯一位 → 不合法", () => {
    expect(isValidNationalId("A123456788")).toBe(false);
  });

  it("長度錯 / 空字串 → 不合法", () => {
    expect(isValidNationalId("A12345678")).toBe(false);
    expect(isValidNationalId("A1234567890")).toBe(false);
    expect(isValidNationalId("")).toBe(false);
  });

  it("性別碼非 1/2 → 不合法", () => {
    expect(isValidNationalId("A323456789")).toBe(false);
  });

  it("小寫字母 / 首碼非英文 → 不合法", () => {
    expect(isValidNationalId("a123456789")).toBe(false);
    expect(isValidNationalId("1123456789")).toBe(false);
  });

  it("含非數字 → 不合法；前後空白可容忍", () => {
    expect(isValidNationalId("A12345678X")).toBe(false);
    expect(isValidNationalId("  A123456789  ")).toBe(true);
  });
});
