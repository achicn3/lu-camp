// 結帳手帳前景那隻拿筆的手（店主 2026-09-27）：跟動畫裡的人同一套畫法——紙紋上色、手繪墨線、
// 同一件鏽紅外套的袖口。只畫手掌、手指、一點手腕與筆；筆尖在這張圖的 (PEN_TIP.x, PEN_TIP.y)。
import { forwardRef } from "react";

export const PEN_TIP = { x: 10, y: 10 };
export const PEN_HAND_SIZE = { w: 210, h: 250 };

const INK = "#33261c";
const SKIN = "#efc9a6";
const SKIN_SHADE = "#e0b18d";
const JACKET = "#c2643f";
const JACKET_SHADE = "#a8502f";

const line = { fill: "none", stroke: INK, strokeLinecap: "round", strokeLinejoin: "round", filter: "url(#cs-rough)" } as const;

export const PenHand = forwardRef<SVGSVGElement>(function PenHand(_props, ref) {
  return (
    <svg
      ref={ref}
      className="ledger-hand"
      viewBox={`0 0 ${PEN_HAND_SIZE.w} ${PEN_HAND_SIZE.h}`}
      width={PEN_HAND_SIZE.w}
      height={PEN_HAND_SIZE.h}
      overflow="visible"
      aria-hidden="true"
    >
      {/* 袖子與袖口（從右下角伸進來） */}
      <path d="M150 168 L330 280 L250 380 L112 222 Z" fill={JACKET} filter="url(#cs-pencil)" />
      <path d="M150 168 L330 280 L250 380 L112 222 Z" fill="url(#cs-hatch-fine)" />
      <path d="M112 186 C124 170 146 162 164 170 L176 198 C156 192 134 198 122 212 Z" fill={JACKET_SHADE} filter="url(#cs-pencil)" />
      <path d="M112 186 C124 170 146 162 164 170 L176 198 C156 192 134 198 122 212 Z" {...line} strokeWidth={2} />
      <path d="M150 168 L330 280 M112 222 L250 380" {...line} strokeWidth={2.2} />
      {/* 手掌 */}
      <path d="M72 94 C88 82 114 88 130 106 C146 124 158 148 150 170 C140 190 118 196 104 184 C86 168 72 150 62 128 C58 114 62 102 72 94 Z" fill={SKIN} filter="url(#cs-pencil)" />
      <path d="M104 184 C118 196 140 190 150 170 C150 180 144 190 132 194 C120 198 110 192 104 184 Z" fill={SKIN_SHADE} />
      <path d="M72 94 C88 82 114 88 130 106 C146 124 158 148 150 170 C140 190 118 196 104 184 C86 168 72 150 62 128 C58 114 62 102 72 94 Z" {...line} strokeWidth={2.2} />
      {/* 筆：筆身、金屬筆尖、筆夾與一點塑膠反光 */}
      <path d="M22 22 L150 150" stroke="#2d2b30" strokeWidth={9} strokeLinecap="round" />
      <path d="M26 20 L146 140" stroke="#6e6a72" strokeWidth={1.6} strokeLinecap="round" opacity={0.8} />
      <path d="M8 8 L24 16 L16 24 Z" fill="#b8bcbf" stroke={INK} strokeWidth={1.2} strokeLinejoin="round" />
      <circle cx={PEN_TIP.x} cy={PEN_TIP.y} r={1.8} fill={INK} />
      <path d="M118 112 L134 106 L150 124" fill="none" stroke="#9aa0a4" strokeWidth={2.4} strokeLinecap="round" />
      <path d="M22 22 L150 150" {...line} strokeWidth={1} opacity={0.6} />
      {/* 彎起來的三根手指（在筆下面） */}
      <path d="M80 110 C92 110 104 120 106 132 C104 142 92 140 88 132 C84 124 78 118 78 114 Z" fill={SKIN} stroke={INK} strokeWidth={1.6} filter="url(#cs-rough)" />
      <path d="M90 128 C102 130 112 140 112 150 C110 158 100 156 96 148 C92 142 88 136 88 132 Z" fill={SKIN} stroke={INK} strokeWidth={1.6} filter="url(#cs-rough)" />
      <path d="M100 146 C110 150 118 158 118 166 C116 174 106 172 102 164 C98 158 98 152 98 150 Z" fill={SKIN} stroke={INK} strokeWidth={1.6} filter="url(#cs-rough)" />
      {/* 大拇指在筆左側、食指壓在筆上 */}
      <path d="M44 82 C56 76 72 84 82 96 C86 104 78 110 70 104 C62 98 52 94 46 92 C38 90 38 84 44 82 Z" fill={SKIN} filter="url(#cs-pencil)" />
      <path d="M44 82 C56 76 72 84 82 96 C86 104 78 110 70 104 C62 98 52 94 46 92 C38 90 38 84 44 82 Z" {...line} strokeWidth={1.8} />
      <path d="M40 56 C52 52 72 62 88 78 C94 84 90 94 82 92 C68 86 54 76 42 70 C33 66 32 58 40 56 Z" fill={SKIN} filter="url(#cs-pencil)" />
      <path d="M40 56 C52 52 72 62 88 78 C94 84 90 94 82 92 C68 86 54 76 42 70 C33 66 32 58 40 56 Z" {...line} strokeWidth={1.8} />
      <path d="M40 58 C38 62 40 66 44 66" {...line} strokeWidth={1} opacity={0.7} />
      <path d="M64 70 q4 6 2 12 M100 116 q-3 4 -1 8" {...line} strokeWidth={0.9} opacity={0.6} />
    </svg>
  );
});
