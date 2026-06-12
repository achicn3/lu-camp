// 金額輸入驗證（整數元、禁 float；docs/10 §7）。回 null 表示非法。
import { parseNtd } from "@/lib/money";

export function parseAmountInput(
  raw: string,
  options: { allowNegative?: boolean; allowZero?: boolean } = {},
): number | null {
  const parsed = parseNtd(raw);
  if (parsed === null) return null;
  if (!options.allowNegative && parsed < 0) return null;
  if (!options.allowZero && parsed === 0) return null;
  return parsed;
}
