// 溢價率/百分比驗證與格式化純函式（/settings 頁用；無 side effect）。

/**
 * 將 rate 字串夾在 [min, max] 範圍內。所有值皆為小數字串（如 "0.1000"）。
 * 回傳與輸入同精度的字串。
 */
export function clampRate(value: string, min: string, max: string): string {
  const v = parseFloat(value);
  const lo = parseFloat(min);
  const hi = parseFloat(max);
  if (v < lo) return min;
  if (v > hi) return max;
  return value;
}

/**
 * 將小數率字串格式化為百分比顯示（如 "0.1000" → "10%"）。
 */
export function formatPct(rateStr: string): string {
  const pct = parseFloat(rateStr) * 100;
  // 去除浮點誤差：最多 4 位小數
  const rounded = parseFloat(pct.toFixed(4));
  return `${rounded}%`;
}

/**
 * 小數率字串 → 百分比數字字串（不含 %），給輸入框當預設值用。
 *
 * **不要寫成 `parseFloat(rate) * 100`**：JS 浮點會讓 "0.0220" 變成
 * 2.1999999999999997、"0.0290" 變成 2.9000000000000004，輸入框直接把這串數字
 * 顯示給店主看，像是系統把設定值改壞了（實際上 DB 存的 Numeric(5,4) 是對的，
 * 只有畫面錯）。率最多四位小數，收整到四位就把誤差消掉了。
 */
export function ratePercentValue(rateStr: string): string {
  const pct = parseFloat(rateStr) * 100;
  if (!Number.isFinite(pct)) return "0";
  return parseFloat(pct.toFixed(4)).toString();
}

/**
 * 解析使用者輸入的百分比數字（如 "10" 表示 10%）為小數率字串（"0.1000"）。
 * 非法輸入回 null。僅接受 >= 0 的數字。
 */
export function parseRateInput(input: string): string | null {
  const trimmed = input.trim();
  if (trimmed === "") return null;
  if (!/^\d+(\.\d+)?$/.test(trimmed)) return null;
  const pct = parseFloat(trimmed);
  if (pct < 0) return null;
  return (pct / 100).toFixed(4);
}

/**
 * 解析百分比整數輸入（如毛利率 0-99、寄售抽成 0-100）。
 * **嚴格整數**：`"50.5"`/`"50abc"` 一律回 null（不可前綴解析成 50 而靜默存錯值）。
 * 負數或超過 `max` 亦回 null。`max` 預設 99（毛利率：避免 ÷0）；寄售抽成傳 100。
 */
export function parsePctInput(input: string, max = 99): number | null {
  const trimmed = input.trim();
  if (trimmed === "") return null;
  if (!/^\d+$/.test(trimmed)) return null;
  const n = parseInt(trimmed, 10);
  if (n < 0 || n > max) return null;
  return n;
}
