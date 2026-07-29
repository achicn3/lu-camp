"use client";
// 指標／欄位說明的 ⓘ：滑鼠停留看 title、點擊或鍵盤 Enter 展開說明。
//
// 為何不是單純的 `<span title>`：門市用觸控 POS 螢幕，且鍵盤操作者無法 focus 到 span，
// 原生 title 等於只有滑鼠使用者看得到。
//
// 定位：展開後**實際量測**泡泡尺寸再夾進視窗（fixed 座標）。只依按鈕落在左半/右半選展開
// 方向並不夠——窄螢幕上泡泡可能接近整個視窗寬，往哪邊展開都會溢出。
import { useCallback, useLayoutEffect, useRef, useState } from "react";

const VIEWPORT_MARGIN = 8;
const GAP = 6;  // 泡泡與觸發按鈕的間距

export function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLSpanElement>(null);

  const place = useCallback(() => {
    const button = buttonRef.current;
    const pop = popRef.current;
    if (!button || !pop) return;
    const anchor = button.getBoundingClientRect();
    const box = pop.getBoundingClientRect();
    const maxLeft = Math.max(VIEWPORT_MARGIN, window.innerWidth - box.width - VIEWPORT_MARGIN);
    // 垂直也要夾：靠近視窗底部時往上翻，否則說明會被切在畫面下緣（fixed 定位不會自動避讓）。
    const below = anchor.bottom + GAP;
    const fitsBelow = below + box.height <= window.innerHeight - VIEWPORT_MARGIN;
    const top = fitsBelow
      ? below
      : Math.max(VIEWPORT_MARGIN, anchor.top - GAP - box.height);
    setPos({
      top,
      left: Math.min(Math.max(anchor.left, VIEWPORT_MARGIN), maxLeft),
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return undefined;
    // 開啟期間以 rAF 追蹤按鈕的實際座標：捲動、視窗縮放、以及**不改變 body 尺寸的版面位移**
    // （例如刪掉上方鑑價列時，若內容不足、頁面高度由 min-height:100vh 決定，body 尺寸不變，
    // 觀察 body 的 ResizeObserver 不會觸發）一律涵蓋。只在泡泡開著時執行，成本可忽略。
    let frame = 0;
    let lastAnchor = "";
    const tick = () => {
      const rect = buttonRef.current?.getBoundingClientRect();
      const key = rect ? `${Math.round(rect.x)},${Math.round(rect.y)}` : "";
      if (key !== lastAnchor) {
        lastAnchor = key;
        place();
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [open, place]);

  return (
    <span className="info-tip-wrap">
      <button
        ref={buttonRef}
        type="button"
        className="info-tip"
        title={text}
        aria-label={`說明：${text}`}
        aria-expanded={open}
        onClick={() => {
          setPos(null); // 量測完成前不定位，避免先閃在錯的位置
          setOpen((v) => !v);
        }}
      >
        ⓘ
      </button>
      {open && (
        <span
          ref={popRef}
          role="tooltip"
          className="info-tip-pop"
          style={
            pos === null ? { visibility: "hidden", top: 0, left: 0 } : { top: pos.top, left: pos.left }
          }
        >
          {text}
        </span>
      )}
    </span>
  );
}
