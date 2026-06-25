"use client";
// 共用分頁控制（全站清單一致）：無「總筆數」端點，故以「滿頁即可能有下一頁」判斷
// （count === pageSize → 有下一頁；與 /inventory 既有口徑一致）。後端各 list 皆支援 limit/offset。
export function Pagination({
  page,
  count,
  pageSize,
  onPage,
}: {
  page: number;
  count: number;
  pageSize: number;
  onPage: (page: number) => void;
}) {
  const hasNext = count === pageSize;
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
      <span className="hint">第 {page + 1} 頁</span>
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
