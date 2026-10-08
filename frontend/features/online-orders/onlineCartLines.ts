// 線上單帶入 POS 購物車的行（docs/44 §4.3；M1c）。
// 同品項同選項可能有兩行（體驗卡＋一般點），POS 的增減／移除／折扣都以鍵指認，鍵必須各自唯一。
import type { OnlineCart } from "@/features/online-orders/OnlineOrdersPanel";
import { type CartLine, menuLineKey } from "@/features/pos/cart";
import { parseNtd } from "@/lib/money";

/** 一般點的行沿用點磚的鍵（之後再點同一杯會合併）；體驗卡的行另成一組；仍重複的再加行號。 */
export function onlineCartLines(lines: OnlineCart["lines"]): CartLine[] {
  const used = new Set<string>();
  return lines.map((line) => {
    const base = menuLineKey(line.menu_item_id, line.menu_option_ids);
    const preferred = line.experience_id == null ? base : `${base}@E${line.experience_id}`;
    const key = used.has(preferred) ? `${preferred}#${line.line_no}` : preferred;
    used.add(key);
    return {
      key,
      lineType: "MENU",
      description: line.description,
      unitPrice: parseNtd(line.unit_price) ?? 0,
      qty: line.qty,
      menuItemId: line.menu_item_id,
      ...(line.menu_option_ids.length > 0 ? { menuOptionIds: line.menu_option_ids } : {}),
    };
  });
}
