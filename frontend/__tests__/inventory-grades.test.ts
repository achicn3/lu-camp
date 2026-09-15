// 成色與標籤標示（2026-09-16：新增全新未拆）。
import { describe, expect, it } from "vitest";

import {
  GRADE_LABEL,
  SERIALIZED_GRADES,
  gradeShortName,
  labelConditionForGrade,
} from "@/features/inventory/grades";

describe("成色", () => {
  it("全新未拆排在序號品成色的最前面，散裝的 E 不在序號品選項裡", () => {
    expect(SERIALIZED_GRADES).toEqual(["N", "S", "A", "B", "C", "D"]);
    expect(GRADE_LABEL.N).toBe("全新未拆");
  });
});

describe("標籤右下角的全新／二手", () => {
  it("只有全新未拆印「全新」", () => {
    expect(labelConditionForGrade("N")).toBe("全新");
  });

  it.each(["S", "A", "B", "C", "D", "E"] as const)("成色 %s 一律印「二手」", (grade) => {
    // S 是「超熱門搶手貨」，講的是好不好賣，不是新舊——不能因為字面好聽就印成全新。
    expect(labelConditionForGrade(grade)).toBe("二手");
  });
});

describe("成色簡稱（定價提示等窄處用）", () => {
  it("全新未拆不能露出程式代號「N 級」", () => {
    // 店員看不懂 N 是什麼；S–D 本來就是店內慣用說法，保留「X 級」。
    expect(gradeShortName("N")).toBe("全新未拆");
    expect(gradeShortName("A")).toBe("A 級");
    expect(gradeShortName("D")).toBe("D 級");
  });
});
