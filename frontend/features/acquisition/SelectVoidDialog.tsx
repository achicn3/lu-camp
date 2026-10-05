"use client";
// 收購紀錄的買斷單「作廢」：商品勾選視窗開在畫面正中央。可整張作廢的單預設勾好所有可作廢的商品
// （＝整張作廢），取消勾選的保留；整張沖不回的單（購物金已被用掉）不預設全勾。
//
// 為什麼要包成對話視窗：先前區塊直接接在清單＋翻頁列的**下方**，清單有幾十列時落在可視範圍外，
// 店長按了「選品作廢」看起來毫無反應（2026-10-02 正式機：連點 #255、#254、#253，商品其實都載入了）。
import { useEffect, useRef } from "react";

import { VoidAcquisitionSection } from "@/features/acquisition/VoidAcquisitionSection";
import type { components } from "@/lib/api-types";

type VoidResult = components["schemas"]["AcquisitionVoidResult"];

// 內層的作廢確認視窗（VoidConfirmDialog）開著時，Esc 不該把外層整個關掉、丟掉已勾的商品與原因。
const NESTED_CONFIRM_SELECTOR = ".acq-void-dialog";

export function SelectVoidDialog({
  acquisitionId,
  preselectAll,
  onClose,
  onVoided,
}: {
  acquisitionId: number;
  /** 預設勾好所有可作廢的商品（＝整張作廢）；整張沖不回的單傳 false。 */
  preselectAll: boolean;
  onClose: () => void;
  onVoided: (result: VoidResult) => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 關閉後把焦點還給按下去的那顆「作廢」鈕，鍵盤操作不會掉回頁首。
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (panelRef.current?.querySelector(NESTED_CONFIRM_SELECTOR)) return;
      onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      opener?.focus();
    };
  }, [onClose]);

  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={`作廢收購 #${acquisitionId}`}
    >
      <div ref={panelRef} className="acq-select-void-dialog" tabIndex={-1}>
        <div className="acq-select-void-header">
          <div className="acq-select-void-title">
            <h2>作廢收購</h2>
            <span>收購單 #{acquisitionId}</span>
          </div>
          <button type="button" className="acq-select-void-close" aria-label="關閉作廢視窗" title="關閉" onClick={onClose}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </button>
        </div>
        <VoidAcquisitionSection
          acquisitionId={acquisitionId}
          preselectAll={preselectAll}
          showHeading={false}
          onVoided={onVoided}
        />
      </div>
    </div>
  );
}
