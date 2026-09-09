"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDate } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type Grade = components["schemas"]["Grade"];
type GradeStat = components["schemas"]["GradePriceStat"];

const GRADE_LABEL: Record<string, string> = {
  S: "S 全新/未使用",
  A: "A 近全新/精品",
  B: "B 良好",
  C: "C 有使用痕跡",
  D: "D 明顯瑕疵",
  E: "E 散裝",
};

/** 「35」「35–45」——同一個數字不重複講兩次，店員一眼看到的是區間還是定值。 */
function range(min: string | null | undefined, max: string | null | undefined): string | null {
  if (min === null || min === undefined || max === null || max === undefined) return null;
  const lo = parseNtd(min);
  const hi = parseNtd(max);
  if (lo === null || hi === null) return null;
  return lo === hi ? formatNtd(lo) : `${formatNtd(lo)}–${formatNtd(hi)}`;
}

function statLine(stat: GradeStat): string {
  const cost = range(stat.cost_min, stat.cost_max);
  const listed = range(stat.listed_min, stat.listed_max);
  // 買斷一定有成本，但欄位可為 NULL；真的沒有就只講售價，絕不補 0 唬人。
  const parts = [cost === null ? null : `收購 ${cost}`, listed === null ? null : `售價 ${listed}`];
  return parts.filter((p) => p !== null).join("、");
}

/**
 * 收購定價提示：同品牌＋型號以前收多少、賣多少。
 *
 * 只在品牌與型號都選了才查——這兩個是下拉選的、存 id，比對可靠；品名是手打的
 * 自由文字，拿來比對會漏。**只看買斷**：寄售的架上價是跟寄售人談的、店家沒有
 * 收購成本，混進來會誤導定價（裁示 2026-09-09）。
 *
 * 查無歷史就整個不顯示，不要用「查無資料」佔畫面；查詢失敗時同樣安靜消失——
 * 這是刻意的，一個參考用的提示不該把整張收購單擋下來或跳錯誤。
 */
export function PriceHint({
  brandId,
  productModelId,
  grade,
}: {
  brandId: number | null;
  productModelId: number | null;
  grade: Grade | "";
}) {
  const [showAll, setShowAll] = useState(false);

  const hintQuery = useQuery({
    queryKey: ["price-hint", brandId, productModelId],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/serialized-items/price-hint", {
        params: { query: { brand_id: brandId as number, product_model_id: productModelId as number } },
      });
      return data ?? null;
    },
    enabled: brandId !== null && productModelId !== null,
  });

  const hint = hintQuery.data;
  if (!hint || hint.total_count === 0) return null;

  // 生成型別把有預設值的欄位標成 optional，先收斂成 null 再用，TS 才收斂得掉。
  const latest = hint.latest ?? null;
  const mine = hint.grades.find((g) => g.grade === grade) ?? null;
  const others = hint.grades.filter((g) => g.grade !== grade);

  return (
    <div className="price-hint">
      <p className="price-hint-main">
        {mine === null ? (
          <>
            以前收過這款 <strong>{hint.total_count}</strong> 件
            {grade === "" ? "" : `，但沒收過 ${GRADE_LABEL[grade] ?? grade}`}
          </>
        ) : (
          <>
            <strong>{GRADE_LABEL[mine.grade] ?? mine.grade}</strong> 以前收過 {mine.count} 件：
            {statLine(mine)}
          </>
        )}
      </p>

      {latest === null ? null : (
        <p className="price-hint-sub">
          {/* 一定要標成色：最近一次可能是別的成色，不標的話會跟上面那行的區間對不起來。 */}
          最近一次收這款 {formatTaipeiDate(latest.acquired_at)}（
          {GRADE_LABEL[latest.grade] ?? latest.grade}）：
          {latest.cost == null ? "未填收購價" : `收 ${formatNtd(parseNtd(latest.cost) ?? 0)}`}
          、賣 {formatNtd(parseNtd(latest.listed_price) ?? 0)}
        </p>
      )}

      {hint.used_all_time ? (
        <p className="price-hint-sub">近一年沒收過這款，上面是更早以前的紀錄，行情可能已經變了。</p>
      ) : null}

      {others.length === 0 ? null : (
        <>
          <button
            type="button"
            className="price-hint-toggle"
            aria-expanded={showAll}
            onClick={() => setShowAll((v) => !v)}
          >
            {showAll ? "收起各成色行情" : `看各成色行情（另有 ${others.length} 種成色）`}
          </button>
          {showAll ? (
            <table className="price-hint-table">
              <thead>
                <tr>
                  <th scope="col">成色</th>
                  <th scope="col">件數</th>
                  <th scope="col">收購價</th>
                  <th scope="col">上架售價</th>
                </tr>
              </thead>
              <tbody>
                {hint.grades.map((g) => (
                  <tr key={g.grade} className={g.grade === grade ? "price-hint-current" : undefined}>
                    <td>{GRADE_LABEL[g.grade] ?? g.grade}</td>
                    <td>{g.count}</td>
                    <td>{range(g.cost_min, g.cost_max) ?? "—"}</td>
                    <td>{range(g.listed_min, g.listed_max) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </>
      )}
    </div>
  );
}
