// F6 收購表單驗證純邏輯：對齊後端 AcquisitionCreate 的必填/互斥，提交前先擋（回 zh-TW 錯誤）。
// 草稿欄位多為字串（表單狀態）；此處只驗形狀，不送網路。
import { parseNtd } from "@/lib/money";
import type { components } from "@/lib/api-types";

type Grade = components["schemas"]["Grade"];
type Basis = components["schemas"]["BulkAcquisitionBasis"];
type PayoutMethod = components["schemas"]["PayoutMethod"];

export type AcqType = "BUYOUT" | "CONSIGNMENT" | "BULK_LOT";

export interface ItemDraft {
  name: string;
  grade: Grade | "";
  categoryId: number | null;
  brandId: number | null;
  productModelId: number | null;
  listedPrice: string;
  acquisitionCost: string; // 買斷
  commissionPct: string; // 寄售
}

export interface LotDraft {
  name: string;
  categoryId: number | null;
  brandId: number | null;
  acquisitionCost: string;
  acquisitionBasis: Basis | "";
  totalQty: string;
  unitPrice: string;
  label: string;
}

export interface AcquisitionDraft {
  type: AcqType;
  contactId: number | null;
  items: ItemDraft[];
  lot: LotDraft;
  payoutMethod: PayoutMethod;
  payoutSplitCash: string;
  sellerIsMember: boolean;
}

/** 字串為正整數元（> 0）。 */
export function isPositiveIntNtd(input: string): boolean {
  const value = parseNtd(input);
  return value !== null && value > 0;
}

const VALID_GRADES = new Set<string>(["S", "A", "B", "C", "D"]);

export function serializedRowErrors(type: AcqType, index: number, row: ItemDraft): string[] {
  const errors: string[] = [];
  const tag = `第 ${index + 1} 列`;
  if (!row.name.trim()) errors.push(`${tag}：品名必填`);
  if (!VALID_GRADES.has(row.grade)) errors.push(`${tag}：成色必選（S–D）`);
  if (row.categoryId === null) errors.push(`${tag}：分類必選`);
  if (!isPositiveIntNtd(row.listedPrice)) errors.push(`${tag}：上架售價須為正整數元`);
  if (type === "BUYOUT" && !isPositiveIntNtd(row.acquisitionCost)) {
    errors.push(`${tag}：買斷收購價須為正整數元`);
  }
  if (type === "CONSIGNMENT") {
    const pct = parseNtd(row.commissionPct);
    if (pct === null || pct < 0 || pct > 100) errors.push(`${tag}：抽成需介於 0–100`);
  }
  return errors;
}

export function lotErrors(lot: LotDraft): string[] {
  const errors: string[] = [];
  if (!lot.name.trim()) errors.push("散裝：名稱必填");
  if (!isPositiveIntNtd(lot.acquisitionCost)) errors.push("散裝：整堆收購成本須為正整數元");
  if (lot.acquisitionBasis !== "WEIGHT" && lot.acquisitionBasis !== "BAG") {
    errors.push("散裝：收購基準必選（秤斤/整袋）");
  }
  const qty = parseNtd(lot.totalQty);
  if (qty === null || qty <= 0) errors.push("散裝：件數須為正整數");
  if (!isPositiveIntNtd(lot.unitPrice)) errors.push("散裝：每件均一價須為正整數元");
  return errors;
}

/** 撥款驗證（買斷/散裝）：購物金/混合須會員；SPLIT 現金部分 0<cash<total 整數。 */
export function payoutErrors(
  method: PayoutMethod,
  sellerIsMember: boolean,
  payableNtd: number,
  splitCash: string,
): string[] {
  const errors: string[] = [];
  if ((method === "STORE_CREDIT" || method === "SPLIT") && !sellerIsMember) {
    errors.push("購物金/混合撥款的對象必須是會員");
  }
  if (method === "SPLIT") {
    const cash = parseNtd(splitCash);
    if (cash === null || !Number.isInteger(cash) || cash <= 0 || cash >= payableNtd) {
      errors.push("混合撥款的現金部分須為整數且介於 0 與應付總額之間");
    }
  }
  return errors;
}

/** 整體草稿驗證；回 zh-TW 錯誤清單（空＝可送出）。 */
export function validateDraft(draft: AcquisitionDraft): string[] {
  const errors: string[] = [];
  if (draft.contactId === null) errors.push("請先選擇或建立賣方/寄售人");

  if (draft.type === "BULK_LOT") {
    errors.push(...lotErrors(draft.lot));
  } else {
    if (draft.items.length === 0) errors.push("至少需要一列鑑價品項");
    draft.items.forEach((row, i) => errors.push(...serializedRowErrors(draft.type, i, row)));
  }

  if (draft.type !== "CONSIGNMENT") {
    const payable =
      draft.type === "BULK_LOT"
        ? parseNtd(draft.lot.acquisitionCost) ?? 0
        : draft.items.reduce((sum, row) => sum + (parseNtd(row.acquisitionCost) ?? 0), 0);
    errors.push(
      ...payoutErrors(draft.payoutMethod, draft.sellerIsMember, payable, draft.payoutSplitCash),
    );
  }
  return errors;
}
