"use client";
// 待整理上架的成色下拉。快速估價可以先不選成色（docs/42 §13），那時值是 ""：
// 一定要有「請選成色」這一項，否則瀏覽器把第一個（全新）顯示成已選，
// 店員再點全新不會觸發變更、成色一直是空的，上架就被擋（店主 2026-10-09 回報）。
import { GRADE_LABEL, SERIALIZED_GRADES } from "@/features/acquisition/labels";
import type { components } from "@/lib/api-types";

type Grade = components["schemas"]["Grade"];

export function GradeSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: Grade | "";
  onChange: (grade: Grade) => void;
}) {
  return (
    <label className="field">
      <span className="field-label">成色</span>
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value as Grade)}>
        <option value="" disabled>
          請選成色
        </option>
        {SERIALIZED_GRADES.map((g) => (
          <option key={g} value={g}>
            {GRADE_LABEL[g]}
          </option>
        ))}
      </select>
    </label>
  );
}
