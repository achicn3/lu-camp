// 客人點餐頁的純邏輯（docs/44 §4.2；店主 2026-10-02 定案的問候語與顯示方式）。
import { describe, expect, it } from "vitest";

import { greeting, itemBadge, priceText, tableCodeFromPath } from "../src/client/logic";

// 台北時間 = UTC+8
const taipei = (hh: number, mm = 0) => new Date(Date.UTC(2026, 9, 2, hh - 8, mm));

describe("時段問候", () => {
  it.each([
    [5, "早安，今天想喝點什麼？"],
    [10, "早安，今天想喝點什麼？"],
    [11, "午安，下午想來點什麼？"],
    [16, "午安，下午想來點什麼？"],
    [17, "晚安，今晚想來點什麼？"],
    [23, "晚安，今晚想來點什麼？"],
    [2, "晚安，今晚想來點什麼？"],
  ])("台北 %i 點：%s", (hour, text) => {
    expect(greeting(taipei(hour))).toBe(text);
  });

  it("用台北時間判斷，不看手機所在時區", () => {
    // UTC 02:00 = 台北 10:00（早上），不論手機設定在哪個時區
    expect(greeting(new Date(Date.UTC(2026, 9, 2, 2, 0)))).toBe("早安，今天想喝點什麼？");
  });
});

describe("價格", () => {
  it("沒有選項：直接顯示價格；有選項：加「起」", () => {
    expect(priceText({ unit_price: 150, option_groups: [] })).toBe("$150");
    expect(priceText({ unit_price: 150, option_groups: [{}] })).toBe("$150 起");
    expect(priceText({ unit_price: 1200, option_groups: [] })).toBe("$1,200");
  });
});

describe("售完／剩幾份", () => {
  it.each([
    [null, null],
    [0, "今日售完"],
    [3, "剩 3 份"],
  ])("remaining=%s → %s", (remaining, badge) => {
    expect(itemBadge({ remaining })).toBe(badge);
  });
});

describe("桌位碼", () => {
  it("從 /t/<碼> 取出；其他路徑沒有碼", () => {
    expect(tableCodeFromPath("/t/abcDEF123_-xyz789")).toBe("abcDEF123_-xyz789");
    expect(tableCodeFromPath("/t/abcDEF123_-xyz789/")).toBe("abcDEF123_-xyz789");
    expect(tableCodeFromPath("/")).toBeNull();
    expect(tableCodeFromPath("/t/")).toBeNull();
    expect(tableCodeFromPath("/t/has space")).toBeNull();
  });
});
