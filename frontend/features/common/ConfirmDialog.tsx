"use client";
// 站內樣式的確認視窗（取代瀏覽器的 window.confirm）。
//
// 為什麼不用 window.confirm：那顆系統小灰框寫不了「為什麼要小心」，長得也不像這個系統，
// 店員在忙的時候容易當成雜訊直接按掉。刪除是不可逆的，值得用同一套視覺、把後果寫清楚。
import type { ReactNode } from "react";

import { useDialogFocus } from "@/features/common/useDialogFocus";

export function ConfirmDialog({
  title,
  body,
  confirmLabel = "確定",
  cancelLabel = "取消",
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 不可逆的動作（刪除）用紅色主鈕，與一般確認區分。 */
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialogRef = useDialogFocus<HTMLDivElement>();
  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      ref={dialogRef}
      tabIndex={-1}
      onKeyDown={(event) => {
        // Esc 一律當成取消：危險動作要容易退出、不容易誤觸。
        if (event.key === "Escape" && !busy) onCancel();
      }}
    >
      <div className="card pos-dialog confirm-dialog">
        <h2>{title}</h2>
        <div className="confirm-dialog-body">{body}</div>
        <div className="pos-dialog-actions">
          <button
            type="button"
            className={danger ? "btn-primary btn-danger" : "btn-primary"}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? "處理中…" : confirmLabel}
          </button>
          <button type="button" className="btn-ghost" disabled={busy} onClick={onCancel}>
            {cancelLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
