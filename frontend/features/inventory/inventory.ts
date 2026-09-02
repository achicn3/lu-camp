// F5 庫存畫面純邏輯（docs/10 §/inventory）：低庫存判定、售出進度、狀態 badge 對應、
// 清單 query 組裝。無 React/DOM 依賴 → 可單元測試。型別一律由 OpenAPI 生成型別帶出。
import type { components } from "@/lib/api-types";
import { gradeLabel } from "@/features/inventory/grades";

type SerializedStatus = components["schemas"]["SerializedItemStatus"];
type BulkStatus = components["schemas"]["BulkLotStatus"];
type Ownership = components["schemas"]["OwnershipType"];

export type BadgeTone = "neutral" | "ok" | "warn" | "muted";
export interface Badge {
  label: string;
  tone: BadgeTone;
}

/** 一般商品低庫存：現有量 ≤ 再訂購點（reorder_point）。 */
export function isLowStock(quantityOnHand: number, reorderPoint: number): boolean {
  return quantityOnHand <= reorderPoint;
}

/** 散裝批售出進度（百分比整數）；total ≤ 0 視為 0；remaining 夾在 [0, total]。 */
export function sellThroughPct(totalQty: number, remainingQty: number): number {
  if (totalQty <= 0) return 0;
  const sold = Math.max(0, Math.min(totalQty, totalQty - remainingQty));
  return Math.round((sold / totalQty) * 100);
}

// Record<列舉, Badge>：列舉變動時 TS 會在編譯期強制補齊（不漏狀態）。
const SERIALIZED_STATUS: Record<SerializedStatus, Badge> = {
  IN_STOCK: { label: "在庫", tone: "ok" },
  SOLD: { label: "已售出", tone: "muted" },
  RETURNED_TO_CONSIGNOR: { label: "已退寄售人", tone: "neutral" },
  WRITTEN_OFF: { label: "已報廢", tone: "warn" },
};
const BULK_STATUS: Record<BulkStatus, Badge> = {
  ON_SALE: { label: "販售中", tone: "ok" },
  SOLD_OUT: { label: "售罄", tone: "muted" },
  WRITTEN_OFF: { label: "已報廢", tone: "warn" },
};
const OWNERSHIP: Record<Ownership, Badge> = {
  OWNED: { label: "自有", tone: "neutral" },
  CONSIGNMENT: { label: "寄售", tone: "warn" },
};

export function serializedStatusBadge(status: SerializedStatus): Badge {
  return SERIALIZED_STATUS[status];
}
export function bulkStatusBadge(status: BulkStatus): Badge {
  return BULK_STATUS[status];
}
export function ownershipBadge(ownership: Ownership): Badge {
  return OWNERSHIP[ownership];
}
export { gradeLabel };

/** 空字串 → undefined（openapi-fetch 會略過 undefined query 參數；型別安全地「不送空篩選」）。 */
export function orUndefined<T extends string>(value: T | ""): T | undefined {
  return value === "" ? undefined : value;
}

/**
 * 商品備註長度上限，與後端 `String(500)` 及 `NoteUpdateRequest.max_length` 一致。
 * 前端先擋，避免使用者打完一長串才被 422 退回。
 */
export const NOTE_MAX_LENGTH = 500;

/** 是否有實質備註（空字串/純空白不算——否則結帳會跳一個沒有內容的提醒）。 */
export function hasNote(note: string | null | undefined): boolean {
  return typeof note === "string" && note.trim() !== "";
}

/** 列表用單行摘要：折掉換行、超過 maxChars 截斷加省略號。沒有備註回空字串。 */
export function noteSummary(note: string | null | undefined, maxChars: number): string {
  if (!hasNote(note)) return "";
  const flat = (note as string).trim().replace(/\s+/g, " ");
  return flat.length > maxChars ? `${flat.slice(0, maxChars)}…` : flat;
}
