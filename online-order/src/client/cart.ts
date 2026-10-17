import { isRetailLine, type OrderLineInput, priceOrder } from "../pricing";
import type { MenuSnapshot } from "./types";

export type CartLine = OrderLineInput;

/** 購物車放著沒動多久就不再沿用（店主 2026-10-17）：從最後一次動購物車算，不是固定週期，客人點到一半不會被清。 */
export const CART_IDLE_MS = 60 * 60 * 1000;

/** 存在手機上的購物車：連同是哪一桌、最後一次動的時間，換桌或放太久就不沿用。 */
export interface SavedCart { lines: CartLine[]; table: string | null; touched_at: number }

function isCartLine(line: unknown): line is CartLine {
  if (typeof line !== "object" || line === null) return false;
  const value = line as Record<string, unknown>;
  if (Number.isInteger(value.catalog_product_id)) return Number.isInteger(value.qty) && Object.keys(value).length === 2;
  return Number.isInteger(value.item_id) && Number.isInteger(value.qty) && Array.isArray(value.option_ids) &&
    value.option_ids.every(Number.isInteger) && (value.experience_id === undefined || Number.isInteger(value.experience_id));
}

/** 讀回存起來的購物車；格式不對（含舊版只存陣列、沒有時間的）一律當沒有。 */
export function parseSavedCart(raw: string | null): SavedCart | null {
  try {
    const value: unknown = JSON.parse(raw ?? "null");
    if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
    const saved = value as Record<string, unknown>;
    if (!Array.isArray(saved.lines) || !Number.isFinite(saved.touched_at)) return null;
    if (saved.table !== null && typeof saved.table !== "string") return null;
    return { lines: saved.lines.filter(isCartLine), table: saved.table, touched_at: saved.touched_at as number };
  } catch { return null; }
}

/**
 * 這次打開頁面能不能沿用上次的購物車：掃了別桌（或外帶）的碼、或放著超過時限沒動，就從空的開始。
 * `table` 傳 undefined＝不比桌號（訂單頁網址沒有桌號）。`expired`＝真的丟掉了東西，畫面要告訴客人。
 */
export function resumeCart(saved: SavedCart | null, table: string | null | undefined, now: number): { lines: CartLine[]; expired: boolean } {
  if (saved === null || saved.lines.length === 0) return { lines: [], expired: false };
  const idle = now - saved.touched_at;
  const stale = idle < 0 || idle >= CART_IDLE_MS || (table !== undefined && table !== saved.table);
  return stale ? { lines: [], expired: true } : { lines: saved.lines, expired: false };
}

export function checkCart(menu: MenuSnapshot, cart: CartLine[]) {
  return priceOrder(menu, cart);
}

function valid(menu: MenuSnapshot, cart: CartLine[]): CartLine[] {
  const priced = checkCart(menu, cart);
  if (!priced.ok) throw new Error(priced.reason);
  return cart;
}

/** 同一份東西＝同品項同選項同體驗卡（續杯與否也要一樣），或同一個帶回家商品：合併數量。 */
function sameThing(a: CartLine, b: CartLine): boolean {
  if (isRetailLine(a) || isRetailLine(b)) {
    return isRetailLine(a) && isRetailLine(b) && a.catalog_product_id === b.catalog_product_id;
  }
  return a.item_id === b.item_id && a.experience_id === b.experience_id && a.refill === b.refill &&
    a.option_ids.length === b.option_ids.length && a.option_ids.every((id) => b.option_ids.includes(id));
}

function copy(line: CartLine): CartLine {
  return isRetailLine(line) ? { ...line } : { ...line, option_ids: [...line.option_ids] };
}

export function addLine(menu: MenuSnapshot, cart: CartLine[], line: CartLine): CartLine[] {
  const index = cart.findIndex((current) => sameThing(current, line));
  const next = cart.map(copy);
  if (index < 0) next.push(copy(line));
  else next[index] = { ...next[index]!, qty: next[index]!.qty + line.qty };
  return valid(menu, next);
}

export function changeQty(menu: MenuSnapshot, cart: CartLine[], index: number, qty: number): CartLine[] {
  if (index < 0 || index >= cart.length) throw new Error("invalid_line");
  if (qty === 0) return removeLine(cart, index);
  const next = cart.map((line, i) => ({ ...line, qty: i === index ? qty : line.qty }));
  return valid(menu, next);
}

export function removeLine(cart: CartLine[], index: number): CartLine[] {
  return cart.filter((_, i) => i !== index);
}
