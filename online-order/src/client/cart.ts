import { type OrderLineInput, priceOrder } from "../pricing";
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

export function addLine(menu: MenuSnapshot, cart: CartLine[], line: CartLine): CartLine[] {
  const index = cart.findIndex((current) =>
    current.item_id === line.item_id && current.experience_id === line.experience_id &&
    current.option_ids.length === line.option_ids.length &&
    current.option_ids.every((id) => line.option_ids.includes(id)));
  const next = cart.map((current) => ({ ...current, option_ids: [...current.option_ids] }));
  if (index < 0) next.push({ ...line, option_ids: [...line.option_ids] });
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
