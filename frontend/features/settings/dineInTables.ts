// 桌號清單編輯的純邏輯（docs/35）。與後端 `SettingsUpdateRequest._clean_tables` 同一組
// 規則；此處先行擋下，讓管理者當場看到原因而不是送出去才吃 422。

export const MAX_TABLE_LENGTH = 20;
export const MAX_TABLES = 50;

export interface AddTableResult {
  tables: string[];
  /** 沒加成功時的中文原因；成功為 null。 */
  error: string | null;
}

/** 加一個桌號。去頭尾空白、拒空白/重複/超長/超量；不改變既有順序。 */
export function addTable(tables: string[], raw: string): AddTableResult {
  const table = raw.trim();
  if (table === "") return { tables, error: "請輸入桌號" };
  if (table.length > MAX_TABLE_LENGTH) {
    return { tables, error: `單一桌號最長 ${MAX_TABLE_LENGTH} 字` };
  }
  // 重複要擋：POS 會把清單直接排成按鈕，兩顆一模一樣的店員分不出來，
  // 而桌號是存成字串快照的，事後也查不出當時點的是哪一顆。
  if (tables.includes(table)) return { tables, error: `桌號「${table}」已存在` };
  if (tables.length >= MAX_TABLES) return { tables, error: `桌號最多 ${MAX_TABLES} 個` };
  return { tables: [...tables, table], error: null };
}

/** 移除一個桌號。已結帳的歷史交易存的是字串快照，不受影響。 */
export function removeTable(tables: string[], table: string): string[] {
  return tables.filter((t) => t !== table);
}

/** 兩份清單是否相同（含順序）——順序即 POS 按鈕順序，換序也算變更。 */
export function sameTables(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((table, i) => table === b[i]);
}
