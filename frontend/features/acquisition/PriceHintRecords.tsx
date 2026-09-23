"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { GRADE_LABEL } from "@/features/inventory/grades";
import { ITEM_STATUS_LABELS } from "@/features/member/labels";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDate } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type PriceHintRecord = components["schemas"]["PriceHintRecord"];

/** 展開區先給最近幾筆；要更多再按「看全部」。 */
export const RECENT_RECORDS = 5;
const PAGE_SIZE = 20;

function money(value: string | null | undefined, missing: string): string {
  if (value === null || value === undefined) return missing;
  const parsed = parseNtd(value);
  return parsed === null ? missing : formatNtd(parsed);
}

function useRecords(brandId: number, productModelId: number, limit: number, offset: number) {
  return useQuery({
    queryKey: ["price-hint-records", brandId, productModelId, limit, offset],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/serialized-items/price-hint/records", {
        params: { query: { brand_id: brandId, product_model_id: productModelId, limit, offset } },
      });
      if (!data) throw new Error("無法讀取收購紀錄");
      return data;
    },
    // 翻頁時先留著上一頁，表格才不會閃成「讀取中」又跳回來。
    placeholderData: keepPreviousData,
  });
}

function RecordsTable({ label, items }: { label: string; items: PriceHintRecord[] }) {
  return (
    // 手機寬度欄位擠不下：儲存格不換行、表格在自己的框裡橫向捲，整頁不會跟著橫捲。
    <div className="price-hint-scroll">
      <table className="price-hint-table price-hint-records" aria-label={label}>
        <thead>
          <tr>
            <th scope="col">收購日</th>
            <th scope="col">成色</th>
            <th scope="col">收購價</th>
            <th scope="col">上架售價</th>
            <th scope="col">現在</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r, i) => (
            // 同一天同價的兩件完全可能，沒有 id 可用，以位置當 key（清單只讀、不重排）。
            <tr key={`${r.acquired_at}-${i}`}>
              <td>{formatTaipeiDate(r.acquired_at)}</td>
              <td>{GRADE_LABEL[r.grade] ?? r.grade}</td>
              <td>{money(r.cost, "未填")}</td>
              <td>{money(r.listed_price, "—")}</td>
              <td>{ITEM_STATUS_LABELS[r.status] ?? r.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 最近 5 筆；總數超過 5 筆才給「看全部」，點開後分頁列出整個期間。 */
export function RecentRecords({
  brandId,
  productModelId,
  total,
  usedAllTime,
}: {
  brandId: number;
  productModelId: number;
  total: number;
  usedAllTime: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const recent = useRecords(brandId, productModelId, RECENT_RECORDS, 0);

  if (recent.isError) return <p className="price-hint-sub">收購紀錄暫時無法讀取。</p>;
  if (!recent.data) return <p className="price-hint-sub">讀取收購紀錄中…</p>;

  const scope = usedAllTime ? "" : "近一年";
  return (
    <>
      <h4 className="price-hint-heading">最近 {RECENT_RECORDS} 筆</h4>
      <RecordsTable label={`最近 ${RECENT_RECORDS} 筆`} items={recent.data.items} />
      {total <= RECENT_RECORDS ? null : showAll ? (
        <AllRecords brandId={brandId} productModelId={productModelId} onClose={() => setShowAll(false)} />
      ) : (
        <button type="button" className="price-hint-toggle" onClick={() => setShowAll(true)}>
          {`看${scope}全部 ${total} 筆收購紀錄`}
        </button>
      )}
    </>
  );
}

function AllRecords({
  brandId,
  productModelId,
  onClose,
}: {
  brandId: number;
  productModelId: number;
  onClose: () => void;
}) {
  const [page, setPage] = useState(0);
  const query = useRecords(brandId, productModelId, PAGE_SIZE, page * PAGE_SIZE);

  if (query.isError) return <p className="price-hint-sub">收購紀錄暫時無法讀取。</p>;
  if (!query.data) return <p className="price-hint-sub">讀取收購紀錄中…</p>;

  const pages = Math.max(1, Math.ceil(query.data.total / PAGE_SIZE));
  // 翻頁途中表格還是上一頁的內容：頁碼要跟著講「讀取中」，按鈕先鎖住，免得頁碼與內容對不上。
  const loading = query.isPlaceholderData;
  return (
    <div className="price-hint-all">
      <h4 className="price-hint-heading">全部收購紀錄（{query.data.total} 筆，新到舊）</h4>
      <RecordsTable label="全部收購紀錄" items={query.data.items} />
      <div className="price-hint-pager">
        <button type="button" disabled={loading || page === 0} onClick={() => setPage((p) => p - 1)}>
          上一頁
        </button>
        <span>{loading ? `第 ${page + 1} 頁讀取中…` : `第 ${page + 1} / ${pages} 頁`}</span>
        <button
          type="button"
          disabled={loading || page + 1 >= pages}
          onClick={() => setPage((p) => p + 1)}
        >
          下一頁
        </button>
        <button type="button" className="price-hint-toggle" onClick={onClose}>
          收起全部紀錄
        </button>
      </div>
    </div>
  );
}
