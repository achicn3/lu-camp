// 叫號清單的呈現規則（docs/38）。抽成純函式：畫面只負責呈現，規則本身可被測試證偽。
import type { components } from "@/lib/api-types";

export type CallTicket = components["schemas"]["CallTicketRead"];

const SAFE_SCHEMES = ["http:", "https:"];

/**
 * 這個連結會被店員點開——**前端也要擋一次**。
 *
 * 後端已於邊界驗證（422），但舊資料、或日後有別的寫入路徑時，畫面不該把
 * `javascript:` 之類的東西渲染成可點連結。防禦深度，不是重複工。
 */
export function isSafeExternalLink(raw: string): boolean {
  try {
    return SAFE_SCHEMES.includes(new URL(raw).protocol);
  } catch {
    // 相對路徑或格式錯誤 → 不當成可點的外部連結
    return false;
  }
}

/**
 * 清單上顯示的號碼。
 *
 * 今日的就顯示 `#3`；**跨日未完成的加上日期**（`8/18 #7`）——那些單仍留在清單上
 * （客人真的還在等），不標日期會與今日的號碼混淆。
 */
export function ticketLabel(ticket: CallTicket, today: string = todayInTaipei()): string {
  if (ticket.ticket_date === today) return `#${ticket.ticket_no}`;
  const [, month, day] = ticket.ticket_date.split("-");
  return `${Number(month)}/${Number(day)} #${ticket.ticket_no}`;
}

/** 台北營業日的 YYYY-MM-DD（與後端 store_date 同口徑）。 */
export function todayInTaipei(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}
