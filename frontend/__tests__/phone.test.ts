// 手機正規化與驗證（前端即時擋，後端仍會再驗一次）。裁示 2026-09-16：只收 09 開頭 10 碼。
import { describe, expect, it } from "vitest";

import { PHONE_HINT, isValidMobile, normalizeMobile } from "@/lib/phone";

describe("normalizeMobile", () => {
  it.each([
    ["0912345678", "0912345678"],
    ["0912-345-678", "0912345678"],
    ["0912 345 678", "0912345678"],
    [" 0912345678 ", "0912345678"],
    ["０９１２３４５６７８", "0912345678"], // 全形（從 Excel／LINE 複製很常見）
  ])("%s → %s", (raw, expected) => {
    expect(normalizeMobile(raw)).toBe(expected);
  });

  it("不合法的一律回 null，不做半套修補", () => {
    // 回傳 null 而不是「盡量湊一個」：湊出來的號碼打不通，比擋下來更糟。
    for (const bad of ["", "   ", "0911", "09123456789", "0812345678", "02-1234-5678", "09abcd5678"]) {
      expect(normalizeMobile(bad)).toBeNull();
    }
  });
});

describe("isValidMobile", () => {
  it("接受各種寫法的同一支號碼", () => {
    expect(isValidMobile("0912-345-678")).toBe(true);
    expect(isValidMobile("0912345678")).toBe(true);
  });

  it("擋下市話與不完整號碼", () => {
    expect(isValidMobile("02-1234-5678")).toBe(false);
    expect(isValidMobile("0911")).toBe(false);
  });
});

describe("PHONE_HINT", () => {
  it("提示要講得出正確格式，店員才知道要打什麼", () => {
    expect(PHONE_HINT).toContain("09");
    expect(PHONE_HINT).toContain("10");
  });
});
