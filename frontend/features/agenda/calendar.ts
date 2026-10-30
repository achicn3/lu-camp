// 行事曆的純計算（docs/99 §9）：月曆格、跨天事件展開、重複規則的白話說明、顏色分類。
// 型別一律取自 OpenAPI 生成物（CLAUDE.md §3），這裡不手刻 API 型別。
import type { components } from "@/lib/api-types";
import { shiftIsoDate } from "@/lib/datetime";

export type AgendaItem = components["schemas"]["AgendaItemRead"];
export type AgendaToday = components["schemas"]["AgendaTodayRead"];
export type AgendaTask = components["schemas"]["AgendaTaskRead"];
export type AgendaSeries = components["schemas"]["AgendaSeriesRead"];
export type AgendaEvent = components["schemas"]["AgendaEventRead"];
export type RecurrenceFields = components["schemas"]["RecurrenceFields"];
export type RecurrenceKind = components["schemas"]["RecurrenceKind"];
export type AgendaEventKind = components["schemas"]["AgendaEventKind"];
export type StaffMember = components["schemas"]["StaffMemberRead"];

/** 畫面上的星期（週日開頭，同一般月曆）。 */
export const WEEK_HEADER = ["日", "一", "二", "三", "四", "五", "六"];
/** 後端的星期：0＝週一 … 6＝週日（同 Python `date.weekday()`）。 */
export const PY_WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

export const EVENT_KIND_LABEL: Record<AgendaEventKind, string> = {
  CLOSED: "公休",
  TEMP_CLOSED: "臨時店休",
  MARKET: "市集／擺攤",
  OTHER: "其他",
};

export const RECURRENCE_KIND_LABEL: Record<RecurrenceKind, string> = {
  WEEKLY: "每週",
  MONTHLY: "每月",
  EVERY_N_MONTHS: "每幾個月",
  YEARLY: "每年",
};

function utcDate(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`);
}

/** 星期幾（後端口徑：0＝週一 … 6＝週日）。 */
export function pyWeekday(iso: string): number {
  return (utcDate(iso).getUTCDay() + 6) % 7;
}

/** 「11/10（二）」 */
export function formatDayHeading(iso: string): string {
  const [, month, day] = iso.split("-");
  return `${Number(month)}/${Number(day)}（${WEEK_HEADER[utcDate(iso).getUTCDay()]}）`;
}

/** 「2026/11/10」 */
export function formatSlashDate(iso: string): string {
  return iso.replaceAll("-", "/");
}

export function addMonths(year: number, month: number, delta: number) {
  const index = year * 12 + (month - 1) + delta;
  return { year: Math.floor(index / 12), month: (index % 12 + 12) % 12 + 1 };
}

export function monthStart(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, "0")}-01`;
}

/** 月曆格：從該月第一天所在那週的週日開始，固定六週（每月高度一致，不會跳動）。 */
export function monthGrid(year: number, month: number) {
  const first = monthStart(year, month);
  const start = shiftIsoDate(first, -utcDate(first).getUTCDay());
  const weeks: string[][] = [];
  for (let w = 0; w < 6; w += 1) {
    weeks.push(Array.from({ length: 7 }, (_, d) => shiftIsoDate(start, w * 7 + d)));
  }
  return { start, end: weeks[5][6], weeks };
}

export function listDays(start: string, count: number): string[] {
  return Array.from({ length: count }, (_, i) => shiftIsoDate(start, i));
}

/** 依日期分組；跨天事件在範圍內的每一天都出現。順序沿用後端（日期、時間、類型）。 */
export function spreadByDay(
  items: AgendaItem[],
  start: string,
  end: string,
): Map<string, AgendaItem[]> {
  const byDay = new Map<string, AgendaItem[]>();
  const push = (day: string, it: AgendaItem) => {
    if (day < start || day > end) return;
    const list = byDay.get(day);
    if (list) list.push(it);
    else byDay.set(day, [it]);
  };
  for (const it of items) {
    const last = it.end_day ?? it.day;
    for (let day = it.day; day <= last; day = shiftIsoDate(day, 1)) push(day, it);
  }
  return byDay;
}

/** 同一天裡：還沒做的排前面，做完、略過的排後面（其餘順序照後端）。 */
export function openFirst(items: AgendaItem[]): AgendaItem[] {
  const resolved = (it: AgendaItem) => (it.status === "DONE" || it.status === "SKIPPED" ? 1 : 0);
  return [...items].sort((a, b) => resolved(a) - resolved(b));
}

/** 剛按下勾選、伺服器還沒回應的那一列（同一時間只會有一列在送出）。 */
export type PendingToggle = { key: string; done: boolean } | null;

export function pendingDoneFor(pending: PendingToggle, item: AgendaItem): boolean | null {
  return pending !== null && pending.key === item.key ? pending.done : null;
}

/** 重複規則的白話說明，例：「每週一、四」「每月 5 號」「每 2 個月的 10 號」「每年 3/1」。 */
export function recurrenceSummary(rule: RecurrenceFields): string {
  let text: string;
  if (rule.kind === "WEEKLY") {
    const days = [...(rule.weekdays ?? [])].sort((a, b) => a - b);
    text = `每週${days.map((d) => PY_WEEKDAY_LABELS[d]).join("、")}`;
  } else if (rule.kind === "MONTHLY") {
    const day = rule.day_of_month ?? 1;
    text = `每月 ${day} 號`;
    if (day >= 29) text += `（沒有 ${day} 號的月份是月底）`;
  } else if (rule.kind === "EVERY_N_MONTHS") {
    text = `每 ${rule.interval_months ?? 2} 個月的 ${rule.day_of_month ?? 1} 號`;
  } else {
    text = `每年 ${rule.month_of_year ?? 1}/${rule.day_of_month ?? 1}`;
  }
  if (rule.until) text += `，到 ${formatSlashDate(rule.until)} 為止`;
  return text;
}

export type ItemTone =
  | "task"
  | "overdue"
  | "done"
  | "closed"
  | "market"
  | "other"
  | "campaign"
  | "purchase";

const PURCHASE_KINDS = new Set(["PO_ORDERED", "PO_EXPECTED", "PO_RECEIVED"]);

/** 採購單的日子（下單、預計到貨、到貨；docs/99 §3、1B）。 */
export function isPurchaseItem(it: AgendaItem): boolean {
  return PURCHASE_KINDS.has(it.kind);
}

/** 顏色分類：待辦看狀態（過期紅、做完淡），事件看類型，活動、進貨各一色（逾期未到也標紅）。 */
export function itemTone(it: AgendaItem): ItemTone {
  if (it.kind === "TASK") {
    if (it.status === "DONE" || it.status === "SKIPPED") return "done";
    return it.overdue ? "overdue" : "task";
  }
  if (isPurchaseItem(it)) return it.overdue ? "overdue" : "purchase";
  if (it.kind === "EVENT") {
    if (it.event_kind === "CLOSED" || it.event_kind === "TEMP_CLOSED") return "closed";
    return it.event_kind === "MARKET" ? "market" : "other";
  }
  return "campaign";
}

/** 行事曆格子裡的一行字：活動加上「開始／最後一天」，事件加時間。 */
export function itemLabel(it: AgendaItem): string {
  if (it.kind === "CAMPAIGN_START") return `活動開始：${it.title}`;
  if (it.kind === "CAMPAIGN_END") return `活動最後一天：${it.title}`;
  if (it.kind === "PO_ORDERED") return `下單：${it.title}`;
  if (it.kind === "PO_EXPECTED") return `預計到貨：${it.title}`;
  if (it.kind === "PO_RECEIVED") return `到貨：${it.title}`;
  const time = it.start_time ? `${it.start_time.slice(0, 5)} ` : "";
  return `${time}${it.title}`;
}

export type OverdueGroup = { key: string; title: string; seriesId: number | null; items: AgendaItem[] };

/** 「已過期」分組：同一個重複待辦的好幾次合成一組（可一鍵全部略過），單次待辦各自一組。 */
export function groupOverdue(items: AgendaItem[]): OverdueGroup[] {
  const groups: OverdueGroup[] = [];
  const bySeries = new Map<number, OverdueGroup>();
  for (const it of items) {
    if (it.series_id !== null && it.series_id !== undefined) {
      const existing = bySeries.get(it.series_id);
      if (existing) {
        existing.items.push(it);
        continue;
      }
      const group = { key: `series:${it.series_id}`, title: it.title, seriesId: it.series_id, items: [it] };
      bySeries.set(it.series_id, group);
      groups.push(group);
    } else {
      groups.push({ key: it.key, title: it.title, seriesId: null, items: [it] });
    }
  }
  return groups;
}
