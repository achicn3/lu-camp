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

// ── 桌號低消（2026-10-14 店主）：每桌可選「整筆最低金額」或「每人至少一份」；沒設＝不限制。
// 規則由雲端點餐在送單時檢查；改完要到「餐飲菜單 → 線上發布」重新發佈才會生效。

export const MAX_MINIMUM_AMOUNT = 9_999_999;

export type MinimumMode = "none" | "amount" | "per_person_item";

/** 編輯中的一桌：金額先保留輸入字串，送出時才驗證。 */
export interface MinimumDraft {
  mode: MinimumMode;
  amount: string;
}

export type MinimumRule = { mode: "amount"; amount: string } | { mode: "per_person_item" };

export type MinimumRules = Record<string, MinimumRule>;

/** 設定讀回 → 每一桌的編輯狀態（沒設的桌號為「不設低消」）。 */
export function minimumDrafts(
  tables: string[],
  rules: Record<string, { mode: string; amount?: string }> | undefined,
): Record<string, MinimumDraft> {
  return Object.fromEntries(
    tables.map((table) => {
      const rule = rules?.[table];
      if (rule?.mode === "amount") return [table, { mode: "amount", amount: rule.amount ?? "" }];
      if (rule?.mode === "per_person_item") return [table, { mode: "per_person_item", amount: "" }];
      return [table, { mode: "none", amount: "" }];
    }),
  );
}

/** 組成要送出的規則；只含目前清單裡、有設低消的桌號。金額不對回第一個錯誤。 */
export function buildMinimums(
  tables: string[],
  drafts: Record<string, MinimumDraft>,
): { minimums: MinimumRules; error: string | null } {
  const minimums: MinimumRules = {};
  for (const table of tables) {
    const draft = drafts[table];
    if (draft === undefined || draft.mode === "none") continue;
    if (draft.mode === "per_person_item") {
      minimums[table] = { mode: "per_person_item" };
      continue;
    }
    const amount = draft.amount.trim();
    if (!/^\d+$/.test(amount) || Number(amount) < 1 || Number(amount) > MAX_MINIMUM_AMOUNT) {
      return { minimums: {}, error: `桌號 ${table} 的最低金額請填 1 以上的整數元` };
    }
    minimums[table] = { mode: "amount", amount: String(Number(amount)) };
  }
  return { minimums, error: null };
}

/** 兩份規則是否相同（金額以數值比對）。 */
export function sameMinimums(
  a: Record<string, { mode: string; amount?: string }> | undefined,
  b: Record<string, { mode: string; amount?: string }> | undefined,
): boolean {
  const left = a ?? {};
  const right = b ?? {};
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  return [...keys].every((key) => {
    const x = left[key];
    const y = right[key];
    if (x === undefined || y === undefined) return x === y;
    return x.mode === y.mode && Number(x.amount ?? 0) === Number(y.amount ?? 0);
  });
}
