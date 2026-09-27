// 結帳手帳前景那隻寫字的手（店主 2026-09-27 提供的手繪圖：黑色原子筆、鏽紅針織袖口）。
// 圖檔 public/brand/pen-hand.webp 已裁掉透明邊、縮到 560px；筆尖在圖的左上角。
// 顯示寬 PEN_HAND_SIZE.w，筆尖在顯示座標 (PEN_TIP.x, PEN_TIP.y)。
import { forwardRef } from "react";

export const PEN_HAND_SIZE = { w: 250, h: Math.round((250 * 532) / 560) };
export const PEN_TIP = { x: 1, y: 1 };

export const PenHand = forwardRef<HTMLImageElement>(function PenHand(_props, ref) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- 動畫圖層用原生 img，不需要 next/image 的延遲載入
    <img
      ref={ref}
      className="ledger-hand"
      src="/brand/pen-hand.webp"
      width={PEN_HAND_SIZE.w}
      height={PEN_HAND_SIZE.h}
      alt=""
      aria-hidden="true"
      draggable={false}
    />
  );
});
