"use client";
// 桌號 QR 一鍵列印（店主 2026-10-15）：每桌一張卡（店名、桌號、QR、掃碼說明），勾選要印的桌，
// 按列印叫出瀏覽器列印（A4，一頁六張；畫面上的工具列不會印出來）。重發某桌 QR 後可只印那一桌。
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { qrMatrix, qrPath } from "@/features/menu/tableQr";
import { api } from "@/lib/api";

type Table = { label: string; service_mode: string; url: string };

function TableQrCard({ table, storeName }: { table: Table; storeName: string }) {
  const matrix = useMemo(() => qrMatrix(table.url), [table.url]);
  const takeout = table.service_mode === "TAKEOUT";
  const title = takeout ? table.label : `桌號 ${table.label}`;
  return (
    <article className="table-qr-card" aria-label={`${title} 點餐 QR`}>
      <p className="table-qr-store">{storeName}</p>
      <h2 className="table-qr-title">{title}</h2>
      <svg
        className="table-qr-code"
        role="img"
        aria-label={`${table.label} 的點餐 QR`}
        viewBox={`-4 -4 ${matrix.length + 8} ${matrix.length + 8}`}
        shapeRendering="crispEdges"
      >
        <rect x={-4} y={-4} width={matrix.length + 8} height={matrix.length + 8} fill="#fff" />
        <path d={qrPath(matrix)} fill="#000" />
      </svg>
      <p className="table-qr-hint">手機掃描，直接點餐</p>
      <p className="table-qr-url">{table.url}</p>
    </article>
  );
}

export function TableQrSheet({ initial }: { initial?: string }) {
  const status = useQuery({
    queryKey: ["online-order", "status"],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/online-order/status");
      if (!data) throw new Error("讀取桌號失敗，請重新整理再試一次。");
      return data;
    },
  });
  // null＝還沒動過：指定了存在的桌就只印那桌，否則全印。
  const [picked, setPicked] = useState<string[] | null>(null);

  if (status.isError) return <p role="alert" className="form-error">{status.error.message}</p>;
  const s = status.data;
  if (s === undefined) return <p className="hint">載入中…</p>;
  if (!s.configured) return <p className="hint">尚未設定線上點餐的雲端網址，印不出 QR。請聯絡管理者。</p>;
  if (s.tables.length === 0) {
    return <p className="hint">還沒有桌號：請先到「設定」加內用桌號，再發佈到線上點餐。</p>;
  }
  const labels = s.tables.map((t) => t.label);
  const selected =
    picked ?? (initial !== undefined && labels.includes(initial) ? [initial] : labels);
  const toggle = (label: string) =>
    setPicked(selected.includes(label) ? selected.filter((l) => l !== label) : [...selected, label]);
  const printing = s.tables.filter((t) => selected.includes(t.label));

  return (
    <div className="table-qr">
      <div className="table-qr-toolbar">
        {s.last_published_at === null && (
          <p role="alert" className="form-error">
            還沒發佈過線上點餐：請先回上一頁按「發佈到線上點餐」，印出來的 QR 才掃得開。
          </p>
        )}
        <fieldset className="table-qr-picks">
          <legend>要印哪幾桌</legend>
          {s.tables.map((t) => (
            <label key={t.code}>
              <input type="checkbox" checked={selected.includes(t.label)} onChange={() => toggle(t.label)} />
              {t.label}
            </label>
          ))}
        </fieldset>
        <div className="table-qr-actions">
          <button type="button" className="btn-ghost" onClick={() => setPicked(labels)}>
            全選
          </button>
          <button type="button" className="btn-ghost" onClick={() => setPicked([])}>
            全部不選
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={printing.length === 0}
            onClick={() => window.print()}
          >
            列印 {printing.length} 張
          </button>
        </div>
        <p className="hint">用 A4 紙列印，一頁六張，沿虛線剪下。重發過 QR 的桌一定要重印，舊的已經不能點餐。</p>
      </div>
      <div className="table-qr-sheet">
        {printing.map((t) => (
          <TableQrCard key={t.code} table={t} storeName={s.store_name} />
        ))}
      </div>
    </div>
  );
}
