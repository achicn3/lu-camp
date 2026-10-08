import { isRetailLine, type OrderLineInput, priceOrder } from "../pricing";
import type { MenuSnapshot } from "./types";

export type CartLine = OrderLineInput;

export function checkCart(menu: MenuSnapshot, cart: CartLine[]) {
  return priceOrder(menu, cart);
}

function valid(menu: MenuSnapshot, cart: CartLine[]): CartLine[] {
  const priced = checkCart(menu, cart);
  if (!priced.ok) throw new Error(priced.reason);
  return cart;
}

/** 同一份東西＝同品項同選項同體驗卡，或同一個帶回家商品：合併數量。 */
function sameThing(a: CartLine, b: CartLine): boolean {
  if (isRetailLine(a) || isRetailLine(b)) {
    return isRetailLine(a) && isRetailLine(b) && a.catalog_product_id === b.catalog_product_id;
  }
  return a.item_id === b.item_id && a.experience_id === b.experience_id &&
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
