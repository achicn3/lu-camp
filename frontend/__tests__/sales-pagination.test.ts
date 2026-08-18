import { describe, expect, it } from "vitest";

import { SALES_PAGE_SIZE, nextSalesPageParam } from "@/features/sales/pagination";

const fullPage = () => new Array(SALES_PAGE_SIZE).fill(0);

describe("nextSalesPageParam（docs/36）", () => {
  it("不足一頁＝已到底", () => {
    expect(nextSalesPageParam([1, 2, 3], [[1, 2, 3]])).toBeUndefined();
  });

  it("完全沒有資料也是已到底", () => {
    expect(nextSalesPageParam([], [[]])).toBeUndefined();
  });

  it("抓滿一頁就要能再抓——否則第 201 筆之後從唯一的救援入口永久消失", () => {
    expect(nextSalesPageParam(fullPage(), [fullPage()])).toBe(SALES_PAGE_SIZE);
  });

  it("offset 隨已載入頁數遞增，不會重複抓同一頁", () => {
    expect(nextSalesPageParam(fullPage(), [fullPage(), fullPage()])).toBe(SALES_PAGE_SIZE * 2);
    expect(nextSalesPageParam(fullPage(), [fullPage(), fullPage(), fullPage()])).toBe(
      SALES_PAGE_SIZE * 3,
    );
  });

  it("**剛好整除**時仍要再抓一次才收斂：以總筆數猜會少一頁", () => {
    // 剛好 200 筆時，最後一頁是滿的 → 還要再要一次（拿到空頁）才知道到底了。
    expect(nextSalesPageParam(fullPage(), [fullPage()])).toBe(SALES_PAGE_SIZE);
    expect(nextSalesPageParam([], [fullPage(), []])).toBeUndefined();
  });
});
