"use client";
// 行事曆各個視窗共用的外框：焦點留在視窗內、Esc 關閉、背景不捲動、內容太長時視窗自己捲
// （採購收貨視窗曾因沒有最大高度、按鈕被擠出畫面而按不到）。
import type { ReactNode } from "react";

import { useDialogFocus } from "@/features/common/useDialogFocus";
import { useBodyScrollLock } from "@/lib/useBodyScrollLock";

export function DialogShell({
  title,
  busy = false,
  onClose,
  children,
}: {
  title: string;
  busy?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useDialogFocus<HTMLDivElement>();
  useBodyScrollLock(true);
  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      ref={ref}
      tabIndex={-1}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) onClose();
      }}
    >
      <div className="card pos-dialog agenda-dialog">
        <div className="agenda-dialog-head">
          <h2>{title}</h2>
          <button
            type="button"
            className="btn-ghost"
            aria-label="關閉"
            disabled={busy}
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
