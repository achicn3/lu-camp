// /purchasing 各頁共用的小工具與常數（清單、建立、明細三頁）。
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { canDiscardIdempotencyKey } from "@/lib/idempotency";
import { formatNtd, parseNtd } from "@/lib/money";

export type Supplier = components["schemas"]["SupplierRead"];
export type PurchaseOrder = components["schemas"]["PurchaseOrderRead"];
export type PoStatus = components["schemas"]["PurchaseOrderStatus"];
export type PurchaseOrderReceiveBody = components["schemas"]["ReceivePurchaseOrderRequest"];

export const PAGE_SIZE = 20;
const RECEIVE_ERROR_CODE_HEADER = "X-Lu-Camp-Error-Code";

// 「待收貨」＝ORDERED＋PARTIAL（部分到貨仍有待收量，不可從待收清單消失）。
export const PO_STATUS_FILTERS: { key: string; label: string; statuses: PoStatus[] }[] = [
  { key: "ALL", label: "全部", statuses: [] },
  { key: "DRAFT", label: "草稿", statuses: ["DRAFT"] },
  { key: "OUTSTANDING", label: "待收貨", statuses: ["ORDERED", "PARTIAL"] },
  { key: "RECEIVED", label: "已收貨", statuses: ["RECEIVED"] },
  { key: "CANCELLED", label: "已取消", statuses: ["CANCELLED"] },
];

export function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export function canDiscardReceivePending(response: Response): boolean {
  if (canDiscardIdempotencyKey(response.status)) return true;
  if (response.status !== 409) return false;
  const code = response.headers.get(RECEIVE_ERROR_CODE_HEADER);
  // 精確重播若已有同 key＋同 body 的 receipt，後端會回 200；有穩定代碼的其他 409 均已 rollback。
  return code !== null && code !== "IDEMPOTENCY_KEY_CONFLICT";
}

export function money(value: string): string {
  const parsed = parseNtd(value);
  return parsed === null ? value : formatNtd(parsed);
}

export function dt(value: string | null | undefined): string {
  return formatTaipeiDateTime(value);
}

let draftKeySeq = 0;
export function nextDraftKey(): string {
  draftKeySeq += 1;
  return `pl-${draftKeySeq}`;
}
