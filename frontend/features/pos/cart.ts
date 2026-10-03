// POS 購物車純邏輯（無 React/DOM 依賴，便於單元測試）。
// 金額一律整數元（number），與 API 字串於邊界轉換（lib/money）。docs/10 §5、docs/16 §3.2。
import type { components } from "@/lib/api-types";
import { parseNtd } from "@/lib/money";

type SaleLineType = components["schemas"]["SaleLineType"];
type BulkBasket = components["schemas"]["BulkBasketRead"];

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
  /** 散裝販售籃（ADR-025）：以籃子售出，後端依先進先出分配到各來源。與 bulkLotId 擇一。 */
  bulkBasketId?: number;
  menuItemId?: number;
  /** 餐飲選項（docs/44 §3.6）：由小到大排序；同品項不同選項是不同行。 */
  menuOptionIds?: number[];
  /** bulk 可售上限（remaining_qty），用於數量上限提示；serialized 為 1。 */
  maxQty?: number;
  /** 商業性質：一般銷售或贈品（贈品成交 0 元但照樣扣庫存）。 */
  lineKind?: "NORMAL" | "GIFT";
  giftReasonId?: number;
  giftNote?: string;
  /** 買 N 送 M：店員指定「送這件」（docs/40 P3b）。後端決定是否生效。 */
  promoFree?: boolean;
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
  /**
   * 掃到的條碼（序號品可省略，用 itemCode）。備註後面接「-末三碼」——店員靠條碼末三碼
   * 找包裝放在哪（店主 2026-10-04）。
   */
  barcode?: string;
  /** 商品品牌（結帳完成頁列出帶備註商品的品牌）；沒有品牌為 null。 */
  brandId?: number | null;
}

/** 讀不到備註時顯示的文字：明說是讀取失敗，不要讓店員誤以為「這件沒事」。 */
export const NOTE_UNKNOWN_TEXT = "備註讀取失敗，請到庫存頁確認這件商品有沒有註記";

/**
 * 結帳提醒的「確認範圍」指紋：只由需要提醒的行與其內容決定。
 * 店員確認過一次就不再打擾，但**再掃進/移除需要提醒的商品時指紋會變**，必須重新確認——
 * 否則後加入的「缺充電線」會被前一次的確認默默吃掉。改數量不算變動。
 * 讀取失敗的行也算在內：之後真的讀到備註時內容改變，會再問一次。
 */
/** 條碼末三碼（不足三碼整個照列）。 */
export function barcodeTail(code: string): string {
  return code.trim().slice(-3);
}

/**
 * 顯示用的商品備註：備註後面接「-條碼末三碼」。沒有備註回 null（沒備註的商品不加末三碼）；
 * 不知道條碼（例如還原時沒取到）就只顯示備註。
 */
export function lineNoteText(line: CartLine): string | null {
  const note = trimmedNote(line);
  if (note === null) return null;
  const code = (line.barcode ?? line.itemCode ?? "").trim();
  return code === "" ? note : `${note}-${barcodeTail(code)}`;
}

function trimmedNote(line: CartLine): string | null {
  const note = typeof line.note === "string" ? line.note.trim() : "";
  return note === "" ? null : note;
}

/** 購物車總件數：每一行的數量加總（一頂帳篷＋3 罐瓦斯＝4 件）。 */
export function cartItemCount(lines: CartLine[]): number {
  return lines.reduce((sum, line) => sum + line.qty, 0);
}

export function noteAckFingerprint(lines: CartLine[]): string {
  // 認原始備註、不認顯示用的「-末三碼」：條碼是商品本身的屬性，不是要重新確認的內容。
  return lines
    .flatMap((line) => {
      const note = trimmedNote(line) ?? (line.noteUnknown === true ? NOTE_UNKNOWN_TEXT : null);
      return note === null ? [] : [`${line.key}\u0000${note}`];
    })
    .join("\u0001");
}

/** 結帳提醒的一列：note 已接上「-條碼末三碼」；unknown＝還原時沒問到備註。 */
export interface NotedLine {
  key: string;
  description: string;
  note: string;
  brandId?: number | null;
  unknown?: true;
}

/**
 * 結帳提醒用：挑出需要提醒的行（保持購物車順序）。
 * 包含兩種——有備註的，以及**還原時沒問到備註的**（`unknown`）。
 * 空白備註不算；把讀不到當成沒有，正是要避免的靜默漏提醒。
 */
export function linesWithNotes(lines: CartLine[]): NotedLine[] {
  return lines.flatMap((line): NotedLine[] => {
    const note = lineNoteText(line);
    if (note !== null) {
      return [{ key: line.key, description: line.description, note, brandId: line.brandId }];
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
): { lines: CartLine[]; duplicateSerialized: boolean; cappedAt: number | null } {
  const existing = lines.find((l) => l.key === incoming.key);
  if (existing) {
    if (incoming.lineType === "SERIALIZED") {
      // 序號品唯一：已在車內不可再加（後端售出即鎖，前端先擋）。
      return { lines, duplicateSerialized: true, cappedAt: null };
    }
    const wanted = existing.qty + incoming.qty;
    const qty = clampQty(wanted, existing.maxQty);
    const merged = lines.map((l) => (l.key === incoming.key ? { ...l, qty } : l));
    // 撞到庫存上限要讓畫面講出來：數量停住又沒提示，店員會以為沒掃到而一直重掃（店主 2026-10-01）。
    return { lines: merged, duplicateSerialized: false, cappedAt: qty < wanted ? qty : null };
  }
  return { lines: [...lines, incoming], duplicateSerialized: false, cappedAt: null };
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

/**
 * 餐飲行的購物車鍵：點磚、客顯快照還原、店員暫存還原三條路徑**共用這一支**，
 * 還原後再點同一杯才會合併數量而不是多出一行。沒選項時維持舊形狀 `MENU-{id}`。
 */
export function menuLineKey(menuItemId: number, optionIds: readonly number[] = []): string {
  if (optionIds.length === 0) return `MENU-${menuItemId}`;
  return `MENU-${menuItemId}-${[...optionIds].sort((a, b) => a - b).join(",")}`;
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
    bulk_basket_id: l.bulkBasketId ?? null,
    menu_item_id: l.menuItemId ?? null,
    qty: l.qty,
    // 商業性質（一般銷售／贈品）。贈品 UI 於 P4 加入，這裡先明確送出一般銷售——
    // 後端與客顯購物車以此區分項目，漏送會讓兩邊的項目鍵對不起來。
    line_kind: l.lineKind ?? "NORMAL",
    gift_reason_id: l.giftReasonId ?? null,
    gift_note: l.giftNote ?? null,
    // 後加欄位：沒勾就不送，購物車快照與冪等指紋維持舊形狀。
    ...(l.promoFree ? { promo_free: true } : {}),
    // 餐飲選項同理：沒選項不送，舊的購物車與冪等指紋不受影響（後端也是沒選項就不入指紋）。
    ...(l.menuOptionIds && l.menuOptionIds.length > 0
      ? { menu_option_ids: [...l.menuOptionIds].sort((a, b) => a - b) }
      : {}),
  }));
}

/** 切換某一列的「送這件」指定（買 N 送 M）。 */
export function togglePromoFree(lines: CartLine[], key: string): CartLine[] {
  return lines.map((line) => (line.key === key ? { ...line, promoFree: !line.promoFree } : line));
}

/**
 * 散裝販售籃 → 購物車一行。一籃一行、可售上限是整籃剩餘（多次收購加總），
 * 掃籃子標籤或掃已入籃的舊來源標籤都走這裡，避免舊標籤只賣得到其中一批。
 */
export function basketCartLine(basket: BulkBasket): CartLine {
  if (basket.remaining_qty <= 0) throw new Error(`${basket.name} 已售罄`);
  return {
    key: `K:${basket.id}`,
    lineType: "BULK_LOT",
    description: basket.name,
    unitPrice: parseNtd(basket.unit_price) ?? 0,
    qty: 1,
    bulkBasketId: basket.id,
    maxQty: basket.remaining_qty,
    note: basketNote(basket),
    barcode: basket.code,
    brandId: basket.brand_id,
  };
}

/**
 * 籃子的結帳提醒＝籃子本身的備註＋籃內**還有貨**的各批收購備註（去重）。
 * 收購時寫在那批的「有 3 支彎掉」若只存在來源上，放進籃子後就再也不會被提醒；
 * 已賣完的批次不會再出貨，不必提。都沒有則為 null。
 */
export function basketNote(basket: BulkBasket): string | null {
  const notes = [
    basket.note,
    ...basket.sources
      .filter((source) => source.status === "ON_SALE" && source.remaining_qty > 0)
      .map((source) => source.note),
  ]
    .map((note) => note?.trim() ?? "")
    .filter((note) => note !== "");
  const unique = [...new Set(notes)];
  return unique.length > 0 ? unique.join("；") : null;
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
      promoFree: undefined, // 贈品不參加活動，指定送這件沒有意義
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
