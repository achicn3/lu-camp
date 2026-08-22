/** 代碼 → 中文的共用對照工具。
 *
 * **查不到就原樣顯示**：寧可露出英文代碼讓人發現「這個值沒對照」，
 * 也不要憑空編一個看似正確的中文——那會讓錯誤更難察覺。
 */
export function labelFor(
  map: Record<string, string>,
  value: string | null | undefined,
): string {
  if (value === null || value === undefined) return "—";
  return map[value] ?? value;
}
