// /stocktake 純函式：實點數解析/驗證、差異計算、彙總、確認 payload 與閘門、狀態徽章。
// 差異 = 實點 − 系統快照；空白代表「未點」(不調整)，後端只校正有列入的商品。
import type { components } from "@/lib/api-types";

export type StocktakeStatus = components["schemas"]["StocktakeStatus"];

export type BadgeTone = "neutral" | "ok" | "warn" | "muted";
export interface Badge {
  label: string;
  tone: BadgeTone;
}

const ST_STATUS_BADGES: Record<StocktakeStatus, Badge> = {
  DRAFT: { label: "盤點中", tone: "warn" },
  CONFIRMED: { label: "已確認", tone: "ok" },
};

export function stStatusBadge(status: StocktakeStatus): Badge {
  return ST_STATUS_BADGES[status];
}

/** 解析實點輸入：空白→null（未點）；非負整數→數值；其餘→null。 */
export function parseCount(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  if (!/^\d+$/.test(trimmed)) return null;
  return Number.parseInt(trimmed, 10);
}

/** 輸入校驗：空白允許（視為未點）；非空但非「非負整數」回錯誤訊息。 */
export function countError(raw: string): string | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  if (!/^\d+$/.test(trimmed)) return "實點數必須為非負整數";
  return null;
}

/** 差異 = 實點 − 系統；未點回 null。 */
export function variance(systemQty: number, counted: number | null): number | null {
  return counted === null ? null : counted - systemQty;
}

export interface CountEntry {
  systemQty: number;
  input: string;
}

export interface CountSummary {
  counted: number; // 已輸入實點的列數
  uncounted: number; // 未點列數
  over: number; // 盤盈總件數（正差異絕對值合計）
  short: number; // 盤虧總件數（負差異絕對值合計）
  net: number; // 淨差異（實點 − 系統）合計
}

export function summarize(entries: CountEntry[]): CountSummary {
  let counted = 0;
  let uncounted = 0;
  let over = 0;
  let short = 0;
  let net = 0;
  for (const { systemQty, input } of entries) {
    const v = variance(systemQty, parseCount(input));
    if (v === null) {
      uncounted += 1;
      continue;
    }
    counted += 1;
    net += v;
    if (v > 0) over += v;
    else if (v < 0) short += -v;
  }
  return { counted, uncounted, over, short, net };
}

/** 確認 payload：只送有輸入且合法的實點（未點/非法者略過）。 */
export function buildConfirmCounts(
  entries: (CountEntry & { catalog_product_id: number })[],
): { catalog_product_id: number; counted_qty: number }[] {
  const counts: { catalog_product_id: number; counted_qty: number }[] = [];
  for (const { catalog_product_id, input } of entries) {
    const parsed = parseCount(input);
    if (parsed !== null) counts.push({ catalog_product_id, counted_qty: parsed });
  }
  return counts;
}

/** 可確認：DRAFT、無欄位錯誤、且至少輸入一筆實點。 */
export function canConfirm(status: StocktakeStatus, entries: CountEntry[]): boolean {
  if (status !== "DRAFT") return false;
  if (entries.some((e) => countError(e.input) !== null)) return false;
  return entries.some((e) => parseCount(e.input) !== null);
}
