// 行事曆表單的純函式（docs/99 §4.2）：重複規則草稿 ↔ API 欄位。
// 後端會再檢查一次；這裡先擋，讓店員按「儲存」前就看到哪裡要改。
import type { RecurrenceFields, RecurrenceKind } from "@/features/agenda/calendar";

export type RuleDraft = {
  kind: RecurrenceKind | "NONE";
  weekdays: number[];
  dayOfMonth: string;
  intervalMonths: string;
  monthOfYear: string;
  until: string;
};

export const MAX_LEAD_DAYS = 365;

export function emptyRuleDraft(): RuleDraft {
  return {
    kind: "NONE",
    weekdays: [],
    dayOfMonth: "1",
    intervalMonths: "2",
    monthOfYear: "1",
    until: "",
  };
}

export function ruleDraftFrom(rule: RecurrenceFields | null | undefined): RuleDraft {
  if (!rule) return emptyRuleDraft();
  return {
    kind: rule.kind,
    weekdays: [...(rule.weekdays ?? [])],
    dayOfMonth: String(rule.day_of_month ?? 1),
    intervalMonths: String(rule.interval_months ?? 2),
    monthOfYear: String(rule.month_of_year ?? 1),
    until: rule.until ?? "",
  };
}

function wholeNumber(value: string): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  return Number(value.trim());
}

function inRange(value: string, low: number, high: number): number | null {
  const n = wholeNumber(value);
  return n !== null && n >= low && n <= high ? n : null;
}

// 驗證「每年幾月幾號」用閏年，讓 2/29 合法（平年後端會落到 2/28）。
function daysInMonthLeap(month: number): number {
  return new Date(Date.UTC(2000, month, 0)).getUTCDate();
}

/** 草稿 → API 的重複規則；不重複回 null。有錯回 error（給店員看的一句話）。 */
export function buildRule(draft: RuleDraft): { rule: RecurrenceFields | null; error: string | null } {
  if (draft.kind === "NONE") return { rule: null, error: null };
  const until = draft.until.trim() === "" ? null : draft.until;
  if (draft.kind === "WEEKLY") {
    if (draft.weekdays.length === 0) return { rule: null, error: "每週重複要選星期幾" };
    const weekdays = [...new Set(draft.weekdays)].sort((a, b) => a - b);
    return { rule: { kind: "WEEKLY", weekdays, until }, error: null };
  }
  const day = inRange(draft.dayOfMonth, 1, 31);
  if (day === null) return { rule: null, error: "幾號要填 1 到 31" };
  if (draft.kind === "MONTHLY") {
    return { rule: { kind: "MONTHLY", weekdays: [], day_of_month: day, until }, error: null };
  }
  if (draft.kind === "EVERY_N_MONTHS") {
    const interval = inRange(draft.intervalMonths, 2, 24);
    if (interval === null) {
      return { rule: null, error: "每幾個月要填 2 到 24（每個月請選「每月」）" };
    }
    return {
      rule: {
        kind: "EVERY_N_MONTHS",
        weekdays: [],
        day_of_month: day,
        interval_months: interval,
        until,
      },
      error: null,
    };
  }
  const month = inRange(draft.monthOfYear, 1, 12);
  if (month === null) return { rule: null, error: "月份要填 1 到 12" };
  if (day > daysInMonthLeap(month)) return { rule: null, error: `${month} 月沒有 ${day} 號` };
  return {
    rule: { kind: "YEARLY", weekdays: [], month_of_year: month, day_of_month: day, until },
    error: null,
  };
}

/** 「提前幾天提醒」：0–365 的整數；空白當 0。不合法回 null。 */
export function parseLeadDays(value: string): number | null {
  if (value.trim() === "") return 0;
  return inRange(value, 0, MAX_LEAD_DAYS);
}
