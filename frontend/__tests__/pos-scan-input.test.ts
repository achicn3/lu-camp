// @vitest-environment jsdom
// POS 條碼欄的輸入處理（店主 2026-10-01）：進頁面自動對焦、中文輸入法要提醒、全形英數轉半形。
import { describe, expect, it } from "vitest";

import { looksLikeImeInput, scanFocusFree, toHalfWidth } from "@/lib/pos-scan-input";

describe("toHalfWidth", () => {
  it("全形英數與符號轉成半形（輸入法開全形時掃到的碼）", () => {
    expect(toHalfWidth("Ｓ１－ＡＢＣ")).toBe("S1-ABC");
  });

  it("全形空白轉成一般空白，半形原樣不動", () => {
    expect(toHalfWidth("S1　A")).toBe("S1 A");
    expect(toHalfWidth("S1-0A")).toBe("S1-0A");
  });
});

describe("looksLikeImeInput", () => {
  it("注音符號或中文字＝中文輸入法吃掉了掃碼", () => {
    expect(looksLikeImeInput("ㄋㄅ")).toBe(true);
    expect(looksLikeImeInput("S1-帳")).toBe(true);
    expect(looksLikeImeInput("ˇ")).toBe(true);
  });

  it("英數條碼不算", () => {
    expect(looksLikeImeInput("S1-9BD9F806B1")).toBe(false);
    expect(looksLikeImeInput("")).toBe(false);
  });
});

describe("scanFocusFree", () => {
  it("焦點在頁面本身或導覽連結上 → 可以拉回條碼欄", () => {
    expect(scanFocusFree(null)).toBe(true);
    expect(scanFocusFree(document.body)).toBe(true);
    expect(scanFocusFree(document.createElement("a"))).toBe(true);
  });

  it("店員正在打別的欄位或按對話框按鈕 → 不搶焦點", () => {
    for (const tag of ["input", "textarea", "select", "button"]) {
      expect(scanFocusFree(document.createElement(tag))).toBe(false);
    }
  });
});
