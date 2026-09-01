// 收購同款多件（客人一次帶三頂一樣的帳篷）：畫面上維持一列＋數量欄，送出時才展開成
// N 筆獨立的序號品。
//
// **為什麼是展開而不是存一個數量欄**：三頂帳篷是三件各自獨立的商品——各有自己的條碼
// 標籤、可以分別上架、分別賣掉、分別退貨。存成「一列數量 3」會退化成散裝批（E 級）的
// 語意，那是另一種東西（一堆共用成本、按件扣減、價格互相獨立）。
//
// 展開刻意發生在**送出前**，且切結書的簽名快照用的是同一份展開結果：後端綁定會逐項
// 比對品名與金額，客人簽 1 件、送出 3 件會直接被擋下——數量因此自動被簽名綁住，
// 不需要另外傳一個數量欄位給後端比對。
import { parseNtd } from "@/lib/money";

import type { AcqType, ItemDraft } from "./validation";

/** 一列的最大件數。打錯一個鍵（3 → 30）就建出一堆假存貨，事後要一件件作廢。 */
export const MAX_QTY_PER_ROW = 99;

export type QuantityRow = ItemDraft & { qty: string };

/** 數量字串 → 件數；不合法回 null（不猜、不默默當 1）。 */
function parseQty(input: string): number | null {
  // 只收半形數字：`parseNtd` 之類的寬鬆解析會收下全形「３」，展開時就與店員看到的不同。
  if (!/^[0-9]+$/.test(input.trim())) return null;
  const qty = Number(input.trim());
  return Number.isInteger(qty) && qty >= 1 && qty <= MAX_QTY_PER_ROW ? qty : null;
}

export function qtyErrors(index: number, qty: string): string[] {
  if (parseQty(qty) !== null) return [];
  return [`第 ${index + 1} 列：件數需為 1–${MAX_QTY_PER_ROW} 的整數`];
}

/** 依件數展開成 N 筆一模一樣的品項；數量本身不進 payload（那只是輸入介面的事）。
 *
 * `type` 非買斷時一律當 1 件：件數欄只在買斷顯示，但**切換型別不會清掉值**——
 * 買斷填 3 件後切到寄售，店員看到一列寄售品、系統卻建了三件，畫面上完全沒有線索
 * （Codex 對抗式審查 High）。件數的語意屬於買斷，就在這裡一次收斂。
 */
export function expandByQty(rows: QuantityRow[], type: AcqType = "BUYOUT"): ItemDraft[] {
  return rows.flatMap(({ qty, ...item }) => {
    const count = type === "BUYOUT" ? parseQty(qty) : 1;
    // 不合法的件數在送出前已被 qtyErrors 擋下；這裡 fail closed 回 0 筆，
    // 絕不「當作 1 件」——那會讓一張擋不住的壞資料悄悄變成一件真的存貨。
    return count === null ? [] : Array.from({ length: count }, () => ({ ...item }));
  });
}

/** 應付總額 = Σ(每件收購價 × 件數)。收購價填的是**每件**的價格。
 *  非買斷同上：件數不適用，一律當 1 件。 */
export function rowsPayableTotal(rows: QuantityRow[], type: AcqType = "BUYOUT"): number {
  return rows.reduce((sum, row) => {
    const cost = parseNtd(row.acquisitionCost) ?? 0;
    const count = type === "BUYOUT" ? parseQty(row.qty) : 1;
    return sum + (count === null ? 0 : cost * count);
  }, 0);
}
