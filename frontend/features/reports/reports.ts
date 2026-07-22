// Pure helpers for /reports page (store-credit reports section).
// triggerDownload: create a Blob download via anchor click.
// defaultDateRange: compute sensible from/to ISO strings.

import {
  exclusiveEndOfTaipeiDay,
  shiftIsoDate,
  startOfTaipeiDay,
  taipeiDate,
} from "@/lib/datetime";

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
  const today = taipeiDate(now);
  return {
    from: shiftIsoDate(today, -30),
    to: today,
  };
}

/** Format a Date to YYYY-MM-DD. */
/**
 * 毛利率顯示：後端回小數字串（如 "0.5807"）→ 百分比一位小數（"58.1%"）。
 * null/undefined/空 → "N/A"。店員看的是百分比，不是原始小數。
 */
export function formatRate(rate: string | number | null | undefined): string {
  if (rate === null || rate === undefined || rate === "") return "N/A";
  const n = Number(rate);
  if (Number.isNaN(n)) return "N/A";
  return `${(n * 100).toFixed(1)}%`;
}

export function isoDate(d: Date): string {
  return taipeiDate(d);
}

/**
 * 起始日期（YYYY-MM-DD）→ 台灣當日 00:00 的 UTC 瞬時（帶時區的 ISO，結尾 Z）。
 * 後端 `created_at` 為 timezone-aware，缺少 offset 的 naive datetime 會回 422；固定以
 * `Asia/Taipei` 零時轉 UTC，讓不同瀏覽器與部署主機得到完全相同的報表邊界。
 */
export function startOfDay(isoDateStr: string): string {
  return startOfTaipeiDay(isoDateStr);
}

/**
 * 結束日期（YYYY-MM-DD）→ 台灣「隔日 00:00」的 UTC 瞬時，作為半開區間的排他上界。
 * 後端用 `created_at < date_to`：送隔日零時讓整個結束日（含最後一刻、小數秒）都納入、
 * 又不含隔日任何資料。同樣帶時區（Z），符合後端拒絕 naive datetime 的契約。
 */
export function exclusiveEnd(isoDateStr: string): string {
  return exclusiveEndOfTaipeiDay(isoDateStr);
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

/** Granularity options for financial trends report (includes quarter). */
export const FINANCIAL_GRANULARITY_OPTIONS: {
  value: "day" | "week" | "month" | "quarter";
  label: string;
}[] = [
  { value: "day", label: "日" },
  { value: "week", label: "週" },
  { value: "month", label: "月" },
  { value: "quarter", label: "季" },
];

// -- SVG chart helpers (lightweight, zero-dependency; coordinate scaling only) --

interface ChartScaling {
  min: number;
  max: number;
  step: number;
  ticks: number[];
}

/**
 * Compute a "nice" Y-axis scaling for a set of numeric values.
 * Returns min, max, step, and an array of tick values.
 * Uses a simple "nice numbers" algorithm for human-readable tick marks.
 */
export function computeChartScaling(values: number[]): ChartScaling {
  if (values.length === 0) {
    return { min: 0, max: 100, step: 20, ticks: [0, 20, 40, 60, 80, 100] };
  }

  let dataMin = Math.min(...values);
  let dataMax = Math.max(...values);

  // Handle all-same or all-zero
  if (dataMin === dataMax) {
    if (dataMin === 0) {
      dataMin = 0;
      dataMax = 100;
    } else if (dataMin > 0) {
      dataMin = 0;
      dataMax = dataMin === 0 ? 100 : Math.max(...values) * 1.2;
    } else {
      dataMax = 0;
    }
  }

  // Include 0 in range when all positive
  if (dataMin > 0) dataMin = 0;

  const range = dataMax - dataMin;
  const roughStep = range / 5;
  const magnitude = Math.pow(10, Math.floor(Math.log10(roughStep)));
  const residual = roughStep / magnitude;

  let niceStep: number;
  if (residual <= 1.5) niceStep = 1 * magnitude;
  else if (residual <= 3) niceStep = 2 * magnitude;
  else if (residual <= 7) niceStep = 5 * magnitude;
  else niceStep = 10 * magnitude;

  const niceMin = Math.floor(dataMin / niceStep) * niceStep;
  const niceMax = Math.ceil(dataMax / niceStep) * niceStep;

  const ticks: number[] = [];
  for (let t = niceMin; t <= niceMax + niceStep * 0.5; t += niceStep) {
    ticks.push(Math.round(t * 1e10) / 1e10); // avoid float artifacts
  }

  return { min: niceMin, max: niceMax, step: niceStep, ticks };
}
