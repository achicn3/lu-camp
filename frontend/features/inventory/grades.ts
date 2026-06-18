// 庫存成色顯示文案（S-D 為序號品，E 為散裝批）。收購與庫存共用，避免同一 grade
// 在不同頁面顯示不同語意。
import type { components } from "@/lib/api-types";

type Grade = components["schemas"]["Grade"];

export const GRADE_LABEL: Record<Grade, string> = {
  S: "S 超熱門搶手貨",
  A: "A 近全新/精品",
  B: "B 良好",
  C: "C 普通",
  D: "D 較差",
  E: "E 散裝",
};

export const SERIALIZED_GRADES: Grade[] = ["S", "A", "B", "C", "D"];

export function gradeLabel(grade: Grade): string {
  return GRADE_LABEL[grade];
}
