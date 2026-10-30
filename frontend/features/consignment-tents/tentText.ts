// 寄售帳篷官網資料（docs/97）的純函式：多行文字框 ↔ 一行一項、資料齊不齊、POS 保留提醒文字。
import type { components } from "@/lib/api-types";

type Tent = components["schemas"]["TentListingRead"];

/** 文字框內容拆成一行一項：去掉頭尾空白與空行。 */
export function linesToList(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

export function listToLines(items: readonly string[]): string {
  return items.join("\n");
}

/** 提醒店員還可以補什麼（全部選填，不擋上架）。 */
export function missingInfo(tent: Tent): string[] {
  const gaps: string[] = [];
  if (tent.photos.length === 0) gaps.push("沒照片");
  if (tent.nights === null || tent.nights === undefined) gaps.push("沒填露數");
  if (!tent.color) gaps.push("沒填顏色");
  return gaps;
}

export const WEB_RESERVED_NOTE = "官網標示「已有人保留」，確定要賣給這位客人嗎？";

/** POS 掃到官網保留中的帳篷：把提醒併進商品備註，結帳前的確認視窗就會列出來（提醒不擋）。 */
export function withWebReservedNote(note: string | null | undefined, reserved: boolean): string | null {
  const base = note?.trim() ? note.trim() : null;
  if (!reserved) return base;
  return base === null ? WEB_RESERVED_NOTE : `${WEB_RESERVED_NOTE}；${base}`;
}
