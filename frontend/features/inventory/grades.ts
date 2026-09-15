// 庫存成色顯示文案（N、S-D 為序號品，E 為散裝批）。收購、庫存、定價提示共用，避免同一 grade
// 在不同頁面顯示不同語意。
import type { LabelCondition } from "@/lib/agent";
import type { components } from "@/lib/api-types";

type Grade = components["schemas"]["Grade"];

export const GRADE_LABEL: Record<Grade, string> = {
  N: "全新未拆",
  S: "S 超熱門搶手貨",
  A: "A 近全新/精品",
  B: "B 良好",
  C: "C 普通",
  D: "D 較差",
  E: "E 散裝",
};

// 全新未拆排最前（裁示 2026-09-16）。
export const SERIALIZED_GRADES: Grade[] = ["N", "S", "A", "B", "C", "D"];

export function gradeLabel(grade: Grade): string {
  return GRADE_LABEL[grade];
}

/**
 * 成色簡稱，給定價提示這種窄處用。S–D 是店內慣用的「X 級」說法；**全新未拆沒有字母
 * 可講**，N 只是程式代號，露到畫面上店員看不懂，所以直接寫全稱。
 */
export function gradeShortName(grade: Grade): string {
  return grade === "N" ? GRADE_LABEL.N : `${grade} 級`;
}

/**
 * 序號品／散裝批標籤右下角的「全新／二手」。
 *
 * **只有全新未拆（N）印「全新」**，其餘一律「二手」（裁示 2026-09-16）。特別是 S：它是
 * 「超熱門搶手貨」，講的是好不好賣而不是新舊，不能因為字面好聽就印成全新。
 * （一般商品是採購進來的新品，沒有成色，呼叫端直接給「全新」，不走這裡。）
 */
export function labelConditionForGrade(grade: Grade): LabelCondition {
  return grade === "N" ? "全新" : "二手";
}
