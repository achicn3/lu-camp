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
