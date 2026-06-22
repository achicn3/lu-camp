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
  }));
}
