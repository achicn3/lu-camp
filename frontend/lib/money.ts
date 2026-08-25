// 金額顯示/解析（新台幣整數元，禁止 float 運算；金額一律以字串於 API 傳輸）。
// Phase 0 僅提供最小工具與測試骨架；完整規則見 docs/03、CLAUDE.md §6。

/** 解析使用者輸入或 API 字串為整數元；非法輸入回傳 null。 */
export function parseNtd(input: string): number | null {
  const cleaned = input.replace(/,/g, "").trim();
  if (!/^-?\d+$/.test(cleaned)) {
    return null;
  }
  return Number.parseInt(cleaned, 10);
}

/** 將整數元格式化為含千分位的顯示字串。 */
export function formatNtd(amount: number): string {
  return amount.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

/**
 * 整數元乘 API Decimal 費率後以 ROUND_HALF_UP 收整。
 *
 * 費率維持字串到 BigInt 比值完成，避免 `5000 * Number("0.0003")` 落成
 * 1.4999999999999998 而比後端 Decimal 少一元。
 */
export function roundNtdByRate(amountNtd: number, rate: string): number | null {
  if (!Number.isSafeInteger(amountNtd)) return null;
  const match = /^(\d+)(?:\.(\d{1,4}))?$/.exec(rate.trim());
  if (!match) return null;
  const scale = BigInt(10_000);
  const whole = BigInt(match[1]);
  const fraction = BigInt((match[2] ?? "").padEnd(4, "0"));
  const rateUnits = whole * scale + fraction;
  const numerator = BigInt(amountNtd) * rateUnits;
  const negative = numerator < BigInt(0);
  const magnitude = negative ? -numerator : numerator;
  const rounded = (BigInt(2) * magnitude + scale) / (BigInt(2) * scale);
  return Number(negative ? -rounded : rounded);
}
