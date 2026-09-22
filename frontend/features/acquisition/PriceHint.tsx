"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

// 成色說明一律用共用那份：這裡原本自帶一份，S 寫「全新/未使用」、C 寫「有使用痕跡」，
// 跟店員實際在選的收購下拉（S 超熱門搶手貨、C 普通）互相矛盾；加了全新未拆之後，
// 同一頁出現兩個「全新」會讓人選錯（2026-09-16）。
import { GRADE_LABEL } from "@/features/inventory/grades";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDate } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";
import "./PriceHint.css";

type GradeStat = components["schemas"]["GradePriceStat"];


/** 「35」「35–45」——同一個數字不重複講兩次，店員一眼看到的是區間還是定值。 */
function range(min: string | null | undefined, max: string | null | undefined): string | null {
  if (min === null || min === undefined || max === null || max === undefined) return null;
  const lo = parseNtd(min);
  const hi = parseNtd(max);
  if (lo === null || hi === null) return null;
  return lo === hi ? formatNtd(lo) : `${formatNtd(lo)}–${formatNtd(hi)}`;
}

function combinedRange(stats: GradeStat[], low: "cost_min" | "listed_min", high: "cost_max" | "listed_max"): string | null {
  const mins = stats.flatMap((stat) => {
    const value = stat[low] == null ? null : parseNtd(stat[low]);
    return value === null ? [] : [value];
  });
  const maxs = stats.flatMap((stat) => {
    const value = stat[high] == null ? null : parseNtd(stat[high]);
    return value === null ? [] : [value];
  });
  if (!mins.length || !maxs.length) return null;
  return range(
    mins.reduce((a, b) => a < b ? a : b).toString(),
    maxs.reduce((a, b) => a > b ? a : b).toString(),
  );
}

/**
 * 收購定價提示：同品牌＋型號以前收多少、賣多少。
 *
 * 只在品牌與型號都選了才查——這兩個是下拉選的、存 id，比對可靠；品名是手打的
 * 自由文字，拿來比對會漏。**只看買斷**：寄售的架上價是跟寄售人談的、店家沒有
 * 收購成本，混進來會誤導定價（裁示 2026-09-09）。
 *
 * 區間一律看同型號全部成色、不依店員選的成色篩（裁示 2026-09-22）；各成色明細收在展開表。
 * 僅供參考，不修改表單價格。
 */
export function PriceHint({
  brandId,
  productModelId,
}: {
  brandId: number | null;
  productModelId: number | null;
}) {
  const [showAll, setShowAll] = useState(false);

  const hintQuery = useQuery({
    queryKey: ["price-hint", brandId, productModelId],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/serialized-items/price-hint", {
        params: { query: { brand_id: brandId as number, product_model_id: productModelId as number } },
      });
      if (!data) throw new Error("無法讀取歷史價格");
      return data;
    },
    enabled: brandId !== null && productModelId !== null,
  });

  const hint = hintQuery.data;
  if (brandId === null || productModelId === null) return null;
  if (hintQuery.isError) return <p className="price-hint" role="status">歷史價格暫時無法讀取，可繼續估價。</p>;
  if (!hint) return <p className="price-hint" role="status">讀取歷史價格中…</p>;
  if (hint.total_count === 0) return <p className="price-hint" role="status">這款商品尚無歷史記錄，可直接估價。</p>;

  // 生成型別把有預設值的欄位標成 optional，先收斂成 null 再用，TS 才收斂得掉。
  const latest = hint.latest ?? null;

  return (
    <div className="price-hint" aria-label="歷史價格參考">
      <p className="price-hint-main">同型號以前收過 {hint.total_count} 件（不分成色）：</p>
      <dl className="price-hint-ranges">
        <div>
          <dt>歷史收購價區間</dt>
          <dd>{combinedRange(hint.grades, "cost_min", "cost_max") ?? "無收購價記錄"}</dd>
        </div>
        <div>
          <dt>歷史上架售價區間</dt>
          <dd>{combinedRange(hint.grades, "listed_min", "listed_max") ?? "無上架價記錄"}</dd>
        </div>
      </dl>
      <p className="price-hint-sub">
        {hint.used_all_time ? "全部歷史" : `近 ${hint.window_months} 個月`}・本店買斷商品・金額為新台幣。
        上架售價含稅，依商品目前記錄，非成交價；僅供估價參考。
      </p>

      {latest === null ? null : (
        <p className="price-hint-sub">
          {/* 一定要標成色：最近一次可能是別的成色，不標的話會跟上面那行的區間對不起來。 */}
          最近一次收這款 {formatTaipeiDate(latest.acquired_at)}（
          {GRADE_LABEL[latest.grade] ?? latest.grade}）：
          {range(latest.cost, latest.cost) === null ? "未填收購價" : `收 ${range(latest.cost, latest.cost)}`}
          、上架 {range(latest.listed_price, latest.listed_price) ?? "未填上架價"}
        </p>
      )}

      {hint.used_all_time ? (
        <p className="price-hint-sub">近一年沒收過這款，上面是更早以前的紀錄，行情可能已經變了。</p>
      ) : null}

      {hint.grades.length < 2 ? null : (
        <>
          <button
            type="button"
            className="price-hint-toggle"
            aria-expanded={showAll}
            onClick={() => setShowAll((v) => !v)}
          >
            {showAll ? "收起各成色行情" : `看各成色行情（共 ${hint.grades.length} 種成色）`}
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
                  <tr key={g.grade}>
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
