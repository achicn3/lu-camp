"use client";
// 共用分頁控制（全站清單一致）：沒有「總筆數」時以「滿頁即可能有下一頁」判斷
// （count === pageSize → 有下一頁；與 /inventory 既有口徑一致）。後端各 list 皆支援 limit/offset。
// 端點有回總筆數就傳 total，改顯示「第 X / Y 頁・共 N 筆」（與 /inventory 同一句），
// 並以總頁數決定有沒有下一頁——剛好整頁時舊推測法會多給一個點得下去的空頁。
export function Pagination({
  page,
  count,
  pageSize,
  total,
  unit = "筆",
  onPage,
}: {
  page: number;
  count: number;
  pageSize: number;
  total?: number;
  unit?: string;
  onPage: (page: number) => void;
}) {
  const pages = total === undefined ? null : Math.max(1, Math.ceil(total / pageSize));
  const hasNext = pages === null ? count === pageSize : page + 1 < pages;
  if (page === 0 && !hasNext) return null; // 只有一頁就不顯示控制
  return (
    <div className="pager">
      <button
        type="button"
        className="btn-ghost"
        disabled={page === 0}
        onClick={() => onPage(page - 1)}
      >
        ← 上一頁
      </button>
      <span className="hint">
        {pages === null
          ? `第 ${page + 1} 頁`
          : `第 ${page + 1} / ${pages} 頁・共 ${total} ${unit}`}
      </span>
      <button
        type="button"
        className="btn-ghost"
        disabled={!hasNext}
        onClick={() => onPage(page + 1)}
      >
        下一頁 →
      </button>
    </div>
  );
}
