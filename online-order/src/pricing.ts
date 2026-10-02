// 送單驗價（docs/44 §3.2、§8.1 T4、§8.2）：價格只依雲端目前生效的菜單快照計算，不信任客人送來的任何金額。
// 品名寫法與 POS 後端 `_line_description` 一致（選項依菜單順序；撞名才加群組名）。
import type { MenuItemView, MenuSnapshot } from "./client/types";

export const MAX_LINES = 20;
export const MAX_LINE_QTY = 10;
export const MAX_TOTAL_QTY = 50;

export interface OrderLineInput {
  item_id: number;
  option_ids: number[];
  qty: number;
}

export interface PricedLine {
  item_id: number;
  name: string;
  option_ids: number[];
  unit_price: number;
  qty: number;
  line_total: number;
  /** 品項或所選選項有每日限量：要 POS 先保留（HOLD）才成立。 */
  limited: boolean;
}

export type PriceResult =
  | { ok: true; lines: PricedLine[]; total: number; needsHold: boolean }
  | { ok: false; reason: string; item_id?: number };

function lineName(name: string, picked: { group: string; option: string }[]): string {
  const counts = new Map<string, number>();
  for (const p of picked) counts.set(p.option, (counts.get(p.option) ?? 0) + 1);
  const labels = picked.map((p) => ((counts.get(p.option) ?? 0) > 1 ? `${p.group}${p.option}` : p.option));
  return labels.length === 0 ? name : `${name}（${labels.join("、")}）`;
}

function priceLine(
  item: MenuItemView,
  input: OrderLineInput,
): PricedLine | { reason: string } {
  const chosen = new Set(input.option_ids);
  if (chosen.size !== input.option_ids.length) return { reason: "invalid_options" };
  const known = new Set(item.option_groups.flatMap((g) => g.options.map((o) => o.id)));
  for (const id of chosen) if (!known.has(id)) return { reason: "invalid_options" };
  let unit = item.unit_price;
  let limited = item.remaining !== null;
  const picked: { group: string; option: string }[] = [];
  const ordered: number[] = [];
  for (const group of item.option_groups) {
    const sel = group.options.filter((o) => chosen.has(o.id));
    if (sel.length < group.min_select || sel.length > group.max_select) return { reason: "invalid_options" };
    for (const o of sel) {
      if (!o.available || o.remaining === 0) return { reason: "sold_out" };
      if (o.remaining !== null) limited = true;
      unit += o.price_delta;
      picked.push({ group: group.name, option: o.name });
      ordered.push(o.id);
    }
  }
  return {
    item_id: item.id,
    name: lineName(item.name, picked),
    option_ids: ordered,
    unit_price: unit,
    qty: input.qty,
    line_total: unit * input.qty,
    limited,
  };
}

/** 依菜單驗證並計價。剩餘份數只是「明顯不夠就先擋」，真正的保留由 POS 決定（docs/44 §3.7）。 */
export function priceOrder(menu: MenuSnapshot, inputs: OrderLineInput[]): PriceResult {
  if (inputs.length === 0) return { ok: false, reason: "empty" };
  if (inputs.length > MAX_LINES) return { ok: false, reason: "too_many" };
  let totalQty = 0;
  for (const i of inputs) {
    if (!Number.isInteger(i.qty) || i.qty < 1 || i.qty > MAX_LINE_QTY) return { ok: false, reason: "invalid_qty" };
    totalQty += i.qty;
  }
  if (totalQty > MAX_TOTAL_QTY) return { ok: false, reason: "too_many" };

  const items = new Map(menu.items.map((i) => [i.id, i]));
  const lines: PricedLine[] = [];
  const itemQty = new Map<number, number>();
  const optionQty = new Map<number, number>();
  for (const input of inputs) {
    const item = items.get(input.item_id);
    if (item === undefined || !item.available) return { ok: false, reason: "item_not_found", item_id: input.item_id };
    if (item.remaining === 0) return { ok: false, reason: "sold_out", item_id: item.id };
    const line = priceLine(item, input);
    if ("reason" in line) return { ok: false, reason: line.reason, item_id: item.id };
    lines.push(line);
    itemQty.set(item.id, (itemQty.get(item.id) ?? 0) + input.qty);
    for (const id of line.option_ids) optionQty.set(id, (optionQty.get(id) ?? 0) + input.qty);
  }
  for (const [id, qty] of itemQty) {
    const remaining = items.get(id)?.remaining ?? null;
    if (remaining !== null && qty > remaining) return { ok: false, reason: "sold_out", item_id: id };
  }
  const options = new Map(
    menu.items.flatMap((i) => i.option_groups.flatMap((g) => g.options.map((o) => [o.id, o] as const))),
  );
  for (const [id, qty] of optionQty) {
    const remaining = options.get(id)?.remaining ?? null;
    if (remaining !== null && qty > remaining) return { ok: false, reason: "sold_out" };
  }
  return {
    ok: true,
    lines,
    total: lines.reduce((sum, l) => sum + l.line_total, 0),
    needsHold: lines.some((l) => l.limited),
  };
}
