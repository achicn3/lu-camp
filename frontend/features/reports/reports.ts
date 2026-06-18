// Pure helpers for /reports page (store-credit reports section).
// triggerDownload: create a Blob download via anchor click.
// defaultDateRange: compute sensible from/to ISO strings.

/**
 * Trigger a browser file download from a Blob response.
 * Creates a temporary <a> element, clicks it, then revokes the object URL.
 */
export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

/**
 * Return a default date range: from = 30 days ago (00:00), to = today (23:59:59).
 * Both as ISO 8601 date strings (YYYY-MM-DD) for query param usage.
 */
export function defaultDateRange(now?: Date): { from: string; to: string } {
  const today = now ?? new Date();
  const from = new Date(today);
  from.setDate(from.getDate() - 30);
  return {
    from: isoDate(from),
    to: isoDate(today),
  };
}

/** Format a Date to YYYY-MM-DD. */
export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * 起始日期（YYYY-MM-DD）→ 該地當日 00:00 的 UTC 瞬時（帶時區的 ISO，結尾 Z）。
 * 後端 `created_at` 為 timezone-aware；若送 naive datetime 會被當成伺服器時區解讀，
 * 造成報表視窗邊界偏移（如 +08:00 最多差 8 小時）。以本地零時轉 UTC 瞬時即對齊使用者當日。
 */
export function startOfDay(isoDateStr: string): string {
  return new Date(`${isoDateStr}T00:00:00`).toISOString();
}

/**
 * 結束日期（YYYY-MM-DD）→ 該地「隔日 00:00」的 UTC 瞬時，作為半開區間的排他上界。
 * 後端用 `created_at < date_to`：送隔日零時讓整個結束日（含最後一刻、小數秒）都納入、
 * 又不含隔日任何資料。同樣帶時區（Z），避免 naive datetime 的時區偏移。
 */
export function exclusiveEnd(isoDateStr: string): string {
  const d = new Date(`${isoDateStr}T00:00:00`);
  d.setDate(d.getDate() + 1);
  return d.toISOString();
}

/** Labels for effectiveness metrics (zh-TW). */
export const EFFECTIVENESS_LABELS: Record<string, string> = {
  take_rate: "選用率",
  avg_premium_rate: "平均溢價率",
  beta_retention: "沉澱率 (beta)",
  excess_spend_rate: "超額消費率",
  alpha_incremental: "新增比例 (alpha)",
  gross_margin_m: "毛利率 (m)",
  delta_per_1000: "每千元損益 (delta)",
};

/** Granularity options for flows report. */
export const GRANULARITY_OPTIONS: { value: "day" | "week" | "month"; label: string }[] = [
  { value: "day", label: "日" },
  { value: "week", label: "週" },
  { value: "month", label: "月" },
];
