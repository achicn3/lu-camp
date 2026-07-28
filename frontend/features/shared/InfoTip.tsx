"use client";
// 指標／欄位說明的 ⓘ：滑鼠停留看 title、點擊或鍵盤 Enter 展開說明。
//
// 為何不是單純的 `<span title>`：門市用觸控 POS 螢幕，且鍵盤操作者無法 focus 到 span，
// 原生 title 等於只有滑鼠使用者看得到（Codex）。
//
// 定位：靠視窗左半的按鈕向右展開、右半向左展開，避免長說明被切在畫面外——單向固定對齊
// （不論 left:0 或 right:0）都只會把溢出問題換到另一邊。
import { useState } from "react";

export function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const [alignEnd, setAlignEnd] = useState(false);

  return (
    <span className="info-tip-wrap">
      <button
        type="button"
        className="info-tip"
        title={text}
        aria-label={`說明：${text}`}
        aria-expanded={open}
        onClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          setAlignEnd(rect.left > window.innerWidth / 2);
          setOpen((v) => !v);
        }}
      >
        ⓘ
      </button>
      {open && (
        <span role="tooltip" className={`info-tip-pop${alignEnd ? " info-tip-pop--end" : ""}`}>
          {text}
        </span>
      )}
    </span>
  );
}
