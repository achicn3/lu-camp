// POS 購物車純邏輯（無 React/DOM 依賴，便於單元測試）。
// 金額一律整數元（number），與 API 字串於邊界轉換（lib/money）。docs/10 §5、docs/16 §3.2。
import type { components } from "@/lib/api-types";

type SaleLineType = components["schemas"]["SaleLineType"];

/** 購物車一行。serialized 數量固定 1；catalog/bulk 可調量。 */
export interface CartLine {
  /** 前端用穩定鍵（serialized 用 item_code、catalog/bulk 用 type+id）。 */
  key: string;
  lineType: SaleLineType;
  description: string;
  unitPrice: number;
  qty: number;
  /** 依 line_type 擇一：序號品帶 item_code、catalog 帶 id、bulk 帶 id、menu 帶 id。 */
  itemCode?: string;
  catalogProductId?: number;
  bulkLotId?: number;
  menuItemId?: number;
  /** bulk 可售上限（remaining_qty），用於數量上限提示；serialized 為 1。 */
  maxQty?: number;
  /** 商業性質：一般銷售或贈品（贈品成交 0 元但照樣扣庫存）。 */
  lineKind?: "NORMAL" | "GIFT";
  giftReasonId?: number;
  giftNote?: string;
  /**
   * 商品備註（掃碼時由庫存帶入，唯讀）。行內顯示，並在按下結帳時彙整成提醒對話框，
   * 避免「缺充電線」這種事到交貨才發現。與 giftNote（贈品原因備註）是不同東西。
   */
  note?: string | null;
  /**
   * 還原購物車時**沒問到**這件商品的備註（401／500／斷線；404 不算）。
   * 取不到不等於沒有備註——當成沒有就會讓「先別賣」無聲消失，故結帳提醒要把它
   * 列出來請店員自行查證，而不是靜默放行。
   */
  noteUnknown?: boolean;
}

/** 讀不到備註時顯示的文字：明說是讀取失敗，不要讓店員誤以為「這件沒事」。 */
export const NOTE_UNKNOWN_TEXT = "備註讀取失敗，請到庫存頁確認這件商品有沒有註記";

/**
 * 結帳提醒的「確認範圍」指紋：只由需要提醒的行與其內容決定。
 * 店員確認過一次就不再打擾，但**再掃進/移除需要提醒的商品時指紋會變**，必須重新確認——
 * 否則後加入的「缺充電線」會被前一次的確認默默吃掉。改數量不算變動。
 * 讀取失敗的行也算在內：之後真的讀到備註時內容改變，會再問一次。
 */
export function noteAckFingerprint(lines: CartLine[]): string {
  return linesWithNotes(lines)
    .map((line) => `${line.key}\u0000${line.note}`)
    .join("\u0001");
}

/**
 * 結帳提醒用：挑出需要提醒的行（保持購物車順序）。
 * 包含兩種——有備註的，以及**還原時沒問到備註的**（`unknown`）。
 * 空白備註不算；把讀不到當成沒有，正是要避免的靜默漏提醒。
 */
export function linesWithNotes(
  lines: CartLine[],
): { key: string; description: string; note: string; unknown?: true }[] {
  return lines.flatMap((line) => {
    if (typeof line.note === "string" && line.note.trim() !== "") {
      return [{ key: line.key, description: line.description, note: line.note.trim() }];
    }
    if (line.noteUnknown === true) {
      return [
        {
          key: line.key,
          description: line.description,
          note: NOTE_UNKNOWN_TEXT,
          unknown: true as const,
        },
      ];
    }
    return [];
  });
}

export function lineTotal(line: CartLine): number {
  return line.unitPrice * line.qty;
}

export function cartTotal(lines: CartLine[]): number {
  return lines.reduce((sum, line) => sum + lineTotal(line), 0);
}

/** 加入一行；若同 key 已存在則合併數量（serialized 不可重複加入，回原車並標記重複）。 */
export function addLine(
  lines: CartLine[],
  incoming: CartLine,
): { lines: CartLine[]; duplicateSerialized: boolean } {
  const existing = lines.find((l) => l.key === incoming.key);
  if (existing) {
    if (incoming.lineType === "SERIALIZED") {
      // 序號品唯一：已在車內不可再加（後端售出即鎖，前端先擋）。
      return { lines, duplicateSerialized: true };
    }
    const merged = lines.map((l) =>
      l.key === incoming.key
        ? { ...l, qty: clampQty(l.qty + incoming.qty, l.maxQty) }
        : l,
    );
    return { lines: merged, duplicateSerialized: false };
  }
  return { lines: [...lines, incoming], duplicateSerialized: false };
}

export function removeLine(lines: CartLine[], key: string): CartLine[] {
  return lines.filter((l) => l.key !== key);
}

export function setQty(
  lines: CartLine[],
  key: string,
  qty: number,
): CartLine[] {
  return lines.map((l) =>
    l.key === key ? { ...l, qty: clampQty(qty, l.maxQty) } : l,
  );
}

function clampQty(qty: number, maxQty: number | undefined): number {
  const floored = Math.max(1, Math.trunc(qty));
  return maxQty !== undefined ? Math.min(floored, maxQty) : floored;
}

/** 轉成 POST /sales 的 lines payload。 */
export function toSaleLines(
  lines: CartLine[],
): components["schemas"]["SaleLineCreateRequest"][] {
  return lines.map((l) => ({
    line_type: l.lineType,
    item_code: l.itemCode ?? null,
    catalog_product_id: l.catalogProductId ?? null,
    bulk_lot_id: l.bulkLotId ?? null,
    menu_item_id: l.menuItemId ?? null,
    qty: l.qty,
    // 商業性質（一般銷售／贈品）。贈品 UI 於 P4 加入，這裡先明確送出一般銷售——
    // 後端與客顯購物車以此區分項目，漏送會讓兩邊的項目鍵對不起來。
    line_kind: l.lineKind ?? "NORMAL",
    gift_reason_id: l.giftReasonId ?? null,
    gift_note: l.giftNote ?? null,
  }));
}

/** 贈品列的 key 前綴：同一商品「買 2 ＋ 送 1」是兩列，共用 key 會被合併成一列。 */
const GIFT_KEY_PREFIX = "G:";

export function isGift(line: CartLine): boolean {
  return line.lineKind === "GIFT";
}

/** 把某一列改成贈品（成交 0 元，但照樣出庫）。已是贈品則原樣回傳。 */
export function markAsGift(
  lines: CartLine[],
  key: string,
  gift: { reasonId: number; note?: string },
): CartLine[] {
  return lines.map((line) => {
    if (line.key !== key || isGift(line)) return line;
    return {
      ...line,
      key: `${GIFT_KEY_PREFIX}${line.key}`,
      lineKind: "GIFT",
      giftReasonId: gift.reasonId,
      giftNote: gift.note,
    };
  });
}

/** 取消贈品，改回一般銷售。 */
export function unmarkGift(lines: CartLine[], key: string): CartLine[] {
  return lines.map((line) => {
    if (line.key !== key || !isGift(line)) return line;
    return {
      ...line,
      key: line.key.slice(GIFT_KEY_PREFIX.length),
      lineKind: "NORMAL",
      giftReasonId: undefined,
      giftNote: undefined,
    };
  });
}
