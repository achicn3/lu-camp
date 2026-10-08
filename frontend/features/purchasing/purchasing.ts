// /purchasing 純函式：採購單建單暫存、欄位驗證、金額小計、狀態徽章與收貨閘門。
// 金額一律整數元字串於 API 傳輸（CLAUDE.md §6）；前端僅做整數元運算，不碰 float。
import type { components } from "@/lib/api-types";
import { parseNtd } from "@/lib/money";

export type PurchaseOrderStatus = components["schemas"]["PurchaseOrderStatus"];
export type CatalogProduct = components["schemas"]["CatalogProductRead"];

export type BadgeTone = "neutral" | "ok" | "warn" | "muted";
export interface Badge {
  label: string;
  tone: BadgeTone;
}

const PO_STATUS_BADGES: Record<PurchaseOrderStatus, Badge> = {
  DRAFT: { label: "草稿", tone: "neutral" },
  ORDERED: { label: "已下單", tone: "warn" },
  PARTIAL: { label: "部分到貨", tone: "warn" },
  RECEIVED: { label: "已收貨", tone: "ok" },
  CANCELLED: { label: "已取消", tone: "muted" },
};

export function poStatusBadge(status: PurchaseOrderStatus): Badge {
  return PO_STATUS_BADGES[status];
}

/** 已下單或部分到貨的採購單可（繼續）收貨入庫。 */
export function canReceive(status: PurchaseOrderStatus): boolean {
  return status === "ORDERED" || status === "PARTIAL";
}

/** 僅草稿可送出（→ 已下單）。 */
export function canSubmit(status: PurchaseOrderStatus): boolean {
  return status === "DRAFT";
}

/** 僅草稿/已下單且尚未收任何貨可取消。 */
export function canCancel(status: PurchaseOrderStatus): boolean {
  return status === "DRAFT" || status === "ORDERED";
}

/** 可修改：草稿大家都能改；已下單／部分到貨／已收貨只有管理者（docs/70 §2）；取消的不能改。 */
export function canEdit(status: PurchaseOrderStatus, isManager: boolean): boolean {
  if (status === "CANCELLED") return false;
  return status === "DRAFT" || isManager;
}

/** 收過貨的單（部分到貨／已收貨）修改時多一欄「已收」。 */
export function hasReceipts(status: PurchaseOrderStatus): boolean {
  return status === "PARTIAL" || status === "RECEIVED";
}

/** 某明細的待收數量（訂購 − 已收，不小於 0）。 */
export function lineRemaining(qty: number, receivedQty: number): number {
  return Math.max(qty - receivedQty, 0);
}

/** 採購單明細草稿（建單畫面暫存，尚未送後端）。 */
export interface DraftLine {
  key: string; // 穩定 React key
  product: CatalogProduct;
  qty: number;
  unitCost: string; // 使用者輸入字串（整數元）
  /** 修改既有採購單時：原本那一列的 id（新加的列沒有）。 */
  lineId?: number;
  /** 修改收過貨的採購單時：已收數量（差額由後端自動加減庫存）。 */
  receivedQty?: number;
}

export function supplierNameError(name: string): string | null {
  return name.trim() === "" ? "請輸入供應商名稱" : null;
}

export function unitCostError(raw: string): string | null {
  const trimmed = raw.trim();
  if (trimmed === "") return "請輸入進貨單價";
  const parsed = parseNtd(trimmed);
  if (parsed === null) return "單價必須為整數元";
  if (parsed <= 0) return "單價必須為正整數";
  return null;
}

export function qtyError(qty: number): string | null {
  if (!Number.isInteger(qty) || qty <= 0) return "數量必須為正整數";
  return null;
}

/** 已收數量：0 到訂購數量之間的整數；沒有這欄（建單、未收過貨）一律合法。 */
export function receivedQtyError(line: DraftLine): string | null {
  const received = line.receivedQty;
  if (received === undefined) return null;
  if (!Number.isInteger(received) || received < 0) return "已收數量必須為 0 或正整數";
  if (Number.isInteger(line.qty) && received > line.qty) return "已收不可超過訂購數量";
  return null;
}

/** 單列小計；任一欄位非法回 null（不參與總額）。 */
export function lineTotal(line: DraftLine): number | null {
  if (qtyError(line.qty) !== null) return null;
  const cost = parseNtd(line.unitCost.trim());
  if (cost === null || cost <= 0) return null;
  return line.qty * cost;
}

export function draftTotal(lines: DraftLine[]): number {
  return lines.reduce((sum, line) => sum + (lineTotal(line) ?? 0), 0);
}

/** 可送出採購單：需有供應商、至少一列、且每列數量與單價皆合法。 */
export function canSubmitPo(supplierId: number | null, lines: DraftLine[]): boolean {
  if (supplierId === null || lines.length === 0) return false;
  return lines.every(
    (line) =>
      qtyError(line.qty) === null &&
      unitCostError(line.unitCost) === null &&
      receivedQtyError(line) === null,
  );
}

/** 轉為後端 createPurchaseOrder 的 lines payload（單價正規化為整數元字串）。 */
export function toLinePayload(
  lines: DraftLine[],
): { catalog_product_id: number; qty: number; unit_cost: string }[] {
  return lines.map((line) => ({
    catalog_product_id: line.product.id,
    qty: line.qty,
    unit_cost: String(parseNtd(line.unitCost.trim()) ?? 0),
  }));
}

/** 轉為後端 updatePurchaseOrder 的 lines payload：帶原列 id 與已收數量（整張覆寫）。 */
export function toUpdatePayload(lines: DraftLine[]): {
  id: number | null;
  catalog_product_id: number;
  qty: number;
  received_qty: number;
  unit_cost: string;
}[] {
  return toLinePayload(lines).map((payload, idx) => ({
    ...payload,
    id: lines[idx].lineId ?? null,
    received_qty: lines[idx].receivedQty ?? 0,
  }));
}
