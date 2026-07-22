/** 門市時間規則：API 瞬間是 UTC，畫面與營業日期固定使用台灣時間。 */

export const TAIPEI_TIME_ZONE = "Asia/Taipei";

type DateTimeValue = string | Date | null | undefined;

const dateTimeFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: TAIPEI_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function formatterParts(value: Date): Record<string, string> {
  return Object.fromEntries(
    dateTimeFormatter
      .formatToParts(value)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
}

function asValidDate(value: DateTimeValue): Date | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string" && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatTaipeiDateTime(
  value: DateTimeValue,
  options: { includeSeconds?: boolean } = {},
): string {
  const date = asValidDate(value);
  if (!date) return "—";
  const parts = formatterParts(date);
  const base = `${parts.year}/${parts.month}/${parts.day} ${parts.hour}:${parts.minute}`;
  return options.includeSeconds ? `${base}:${parts.second}` : base;
}

export function formatTaipeiDate(value: DateTimeValue): string {
  const date = asValidDate(value);
  if (!date) return "—";
  const parts = formatterParts(date);
  return `${parts.year}/${parts.month}/${parts.day}`;
}

export function formatTaipeiTime(value: DateTimeValue): string {
  const date = asValidDate(value);
  if (!date) return "—";
  const parts = formatterParts(date);
  return `${parts.hour}:${parts.minute}`;
}

export function taipeiDate(now: Date = new Date()): string {
  const parts = formatterParts(now);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function parseIsoDate(value: string): { year: number; month: number; day: number } {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) throw new Error("日期必須是 YYYY-MM-DD");
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const check = new Date(Date.UTC(year, month - 1, day));
  if (
    check.getUTCFullYear() !== year ||
    check.getUTCMonth() !== month - 1 ||
    check.getUTCDate() !== day
  ) {
    throw new Error("日期不存在");
  }
  return { year, month, day };
}

export function shiftIsoDate(value: string, days: number): string {
  const { year, month, day } = parseIsoDate(value);
  const shifted = new Date(Date.UTC(year, month - 1, day + days));
  return [
    shifted.getUTCFullYear(),
    String(shifted.getUTCMonth() + 1).padStart(2, "0"),
    String(shifted.getUTCDate()).padStart(2, "0"),
  ].join("-");
}

export function startOfTaipeiDay(value: string): string {
  parseIsoDate(value);
  return new Date(`${value}T00:00:00+08:00`).toISOString();
}

export function exclusiveEndOfTaipeiDay(value: string): string {
  return startOfTaipeiDay(shiftIsoDate(value, 1));
}

export function taipeiDateTimeLocalToUtc(value: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!match) throw new Error("日期時間格式不正確");
  parseIsoDate(match[1]);
  const hour = Number(match[2]);
  const minute = Number(match[3]);
  const second = Number(match[4] ?? "0");
  if (hour > 23 || minute > 59 || second > 59) throw new Error("日期時間不存在");
  return new Date(
    `${match[1]}T${match[2]}:${match[3]}:${String(second).padStart(2, "0")}+08:00`,
  ).toISOString();
}
