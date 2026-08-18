// 交易紀錄清單的分頁規則（docs/36）。抽成純函式：畫面只負責呈現，規則本身可被測試證偽。

/** 後端 `/api/v1/sales` 的 `limit` 上限；一頁抓滿再以 `offset` 續抓。 */
export const SALES_PAGE_SIZE = 200;

/**
 * 下一頁的 `offset`；`undefined` 代表已到底。
 *
 * 「只看未開立」不限日期、會隨時間累積，只抓第一頁的話第 201 筆之後就從**唯一的救援
 * 入口**永久消失——而那些正是拖最久、最該處理的單（Codex 對抗審查第十一輪 medium）。
 *
 * 判準是「**這一頁有沒有抓滿**」：不足一頁＝後面沒有了；剛好滿頁則可能還有，
 * 多按一次「載入更多」拿到空頁即收斂。以總筆數猜測會在「剛好整除」時少一頁。
 */
export function nextSalesPageParam(
  lastPage: readonly unknown[],
  allPages: readonly (readonly unknown[])[],
): number | undefined {
  return lastPage.length < SALES_PAGE_SIZE ? undefined : allPages.length * SALES_PAGE_SIZE;
}
