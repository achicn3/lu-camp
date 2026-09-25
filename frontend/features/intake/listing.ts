// 待整理上架（docs/42 §8）：畫面上的草稿、送出時只帶改過的欄位、上架後印標籤。
import { labelConditionForGrade } from "@/features/inventory/grades";
import { printLabel } from "@/lib/agent";
import type { components } from "@/lib/api-types";
import { parseNtd } from "@/lib/money";

type Item = components["schemas"]["IntakeItemRead"];
type Edit = components["schemas"]["IntakeItemEdit"];
type Grade = components["schemas"]["Grade"];

export interface Draft {
  name: string;
  grade: Grade | "";
  brandId: number | null;
  brandName: string | null;
  modelId: number | null;
  modelName: string | null;
  categoryId: number | null;
  categoryName: string | null;
  price: string;
  note: string;
}

export function draftFrom(item: Item): Draft {
  return {
    name: item.name,
    grade: item.grade ?? "",
    brandId: item.brand_id ?? null,
    brandName: item.brand_name ?? null,
    modelId: item.product_model_id ?? null,
    modelName: item.product_model_name ?? null,
    categoryId: item.category_id ?? null,
    categoryName: item.category_name ?? null,
    price: item.listed_price,
    note: item.note ?? "",
  };
}

/** 草稿與原本不同的欄位才送；都沒改回 null。散裝沒有成色與型號。 */
export function editFor(item: Item, draft: Draft): Edit | null {
  const edit: Edit = { kind: item.kind === "BULK_LOT" ? "BULK_LOT" : "SERIALIZED", id: item.id };
  let changed = false;
  const set = <K extends keyof Edit>(key: K, value: Edit[K]) => {
    edit[key] = value;
    changed = true;
  };
  const name = draft.name.trim();
  if (name !== "" && name !== item.name) set("name", name);
  if (draft.brandId !== (item.brand_id ?? null)) set("brand_id", draft.brandId);
  if (draft.categoryId !== null && draft.categoryId !== (item.category_id ?? null)) {
    set("category_id", draft.categoryId);
  }
  const price = draft.price.trim();
  if (price !== "" && parseNtd(price) !== parseNtd(item.listed_price)) set("listed_price", price);
  const note = draft.note.trim();
  if (note !== (item.note ?? "")) set("note", note === "" ? null : note);
  if (edit.kind === "SERIALIZED") {
    if (draft.grade !== "" && draft.grade !== item.grade) set("grade", draft.grade);
    if (draft.modelId !== (item.product_model_id ?? null)) set("product_model_id", draft.modelId);
  }
  return changed ? edit : null;
}

/** 上架前還缺什麼（分類必填；品牌建議填，標籤會印）。 */
export function missingOf(draft: Draft): string[] {
  const missing: string[] = [];
  if (draft.categoryId === null) missing.push("分類");
  if (draft.brandId === null) missing.push("品牌");
  return missing;
}

/** 這次上架的件逐張印標籤（品名、售價、品牌、全新／二手）；回印了幾張。 */
export async function printListedLabels(items: Item[]): Promise<number> {
  for (const item of items) {
    await printLabel(item.code, item.name, parseNtd(item.listed_price) ?? 0, {
      brand: item.brand_name ?? null,
      condition: labelConditionForGrade(item.grade ?? "E"),
    });
  }
  return items.length;
}

/** 收件單條碼（IN000123）或直接打批次編號 → 批次 id；看不懂回 null。 */
export function batchIdFromSlip(input: string): number | null {
  const match = /^(?:IN)?0*(\d+)$/i.exec(input.trim());
  if (!match) return null;
  const id = Number(match[1]);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}
