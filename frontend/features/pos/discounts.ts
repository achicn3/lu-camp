// POS 臨時折扣的純邏輯（無 React/DOM 依賴）。
//
// 後端以**明細順序索引**指定折扣目標，但購物車在畫面上會被移除、加入、重新加總——
// 索引隨時會位移。所以前端一律以購物車列的穩定 key 記錄折扣，只在送出的那一刻換算成索引，
// 並丟掉目標已不在車上的折扣。若直接存索引，店員移除一列就會讓折扣默默跑到別的商品上。
import type { CartLine } from "@/features/pos/cart";
import type { components } from "@/lib/api-types";

type AdjustmentRequest = components["schemas"]["SaleAdjustmentRequest"];

export type DiscountMethod = components["schemas"]["CalculationMethod"];

/** 店員在畫面上加的一筆折扣。`targetKey` 是購物車列的 key（整單折扣為 null）。 */
export interface DiscountDraft {
  /** 本機識別碼，僅供列表 key 與移除用。 */
  id: string;
  scope: components["schemas"]["AdjustmentScope"];
  targetKey: string | null;
  method: DiscountMethod;
  /** 固定金額＝元；百分比＝1–99。 */
  value: number;
  reasonId: number | null;
  note: string | null;
}

/** 丟掉目標已不在購物車上的單品折扣（該商品被移除了）。 */
export function pruneDiscounts(
  drafts: DiscountDraft[],
  lines: CartLine[],
): DiscountDraft[] {
  const keys = new Set(lines.map((line) => line.key));
  return drafts.filter(
    (draft) => draft.targetKey === null || keys.has(draft.targetKey),
  );
}

/** 轉成 API 的 adjustments payload；目標 key 於此刻換算成明細索引。 */
export function toAdjustmentRequests(
  drafts: DiscountDraft[],
  lines: CartLine[],
): AdjustmentRequest[] {
  return pruneDiscounts(drafts, lines).map((draft) => ({
    scope: draft.scope,
    method: draft.method,
    value: String(draft.value),
    target_line_index:
      draft.targetKey === null
        ? null
        : lines.findIndex((line) => line.key === draft.targetKey),
    reason_id: draft.reasonId,
    note: draft.note,
  }));
}

/** 畫面上的折扣說明（例：「整單折扣 10%」「甲 −100 元」）。 */
export function describeDiscount(
  draft: DiscountDraft,
  lines: CartLine[],
): string {
  const amount =
    draft.method === "PERCENTAGE" ? `${draft.value}%` : `−${draft.value} 元`;
  if (draft.scope === "ORDER") return `整單折扣 ${amount}`;
  const line = lines.find((l) => l.key === draft.targetKey);
  return `${line?.description ?? "已移除的商品"} ${amount}`;
}
