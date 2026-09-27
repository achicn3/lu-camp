// 結帳手帳前景那隻寫字的手（店主 2026-09-27 定稿規格 F）：
// 一般握筆姿勢——拇指、食指、中指夾住筆，無名指微微露出，筆尖碰紙；手從右下角進來、手腕貼近紙面，
// 只露一點袖口。畫法跟動畫裡的人一致（紙紋上色、手繪墨線、同一件鏽紅外套），不是真人手也不是圖示。
// 筆尖在這張圖的 (PEN_TIP.x, PEN_TIP.y)。
import { forwardRef } from "react";

export const PEN_TIP = { x: 14, y: 152 };
export const PEN_HAND_SIZE = { w: 190, h: 225 };

const INK = "#33261c";
const SKIN = "#efc9a6";
const SKIN_SHADE = "#dfae8a";
const JACKET = "#c2643f";
const JACKET_SHADE = "#a8502f";

const line = { fill: "none", stroke: INK, strokeLinecap: "round", strokeLinejoin: "round", filter: "url(#cs-rough)" } as const;
const skin = { fill: SKIN, filter: "url(#cs-pencil)" } as const;

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
      {/* 袖子只露一小段（往右下延伸出畫面） */}
      <path d="M150 200 L184 166 L250 232 L214 268 Z" fill={JACKET} filter="url(#cs-pencil)" />
      <path d="M150 200 L184 166 L250 232 L214 268 Z" fill="url(#cs-hatch-fine)" />
      <path d="M146 196 L180 162 L194 176 L160 210 Z" fill={JACKET_SHADE} filter="url(#cs-pencil)" />
      <path d="M150 200 L184 166 L250 232 L214 268 Z M146 196 L180 162 L194 176 L160 210 Z" {...line} strokeWidth={1.8} />
      {/* 中指：在筆下面托著，指根藏在手掌下 */}
      <path d="M38 148 C46 140 62 138 78 140 L100 144 L96 164 L74 158 C62 158 50 158 44 158 C36 158 32 152 38 148 Z" {...skin} />
      <path d="M38 148 C46 140 62 138 78 140 L100 144 M96 164 L74 158 C62 158 50 158 44 158 C36 158 32 152 38 148" {...line} strokeWidth={1.5} />
      {/* 拇指：從手的左側伸出來，捏住筆的左邊 */}
      <path d="M26 130 C32 120 48 116 62 120 L92 130 L86 150 L64 140 C54 140 42 140 34 140 C26 140 22 136 26 130 Z" {...skin} />
      <path d="M26 130 C32 120 48 116 62 120 L92 130 M86 150 L64 140 C54 140 42 140 34 140 C26 140 22 136 26 130" {...line} strokeWidth={1.6} />
      <path d="M28 130 C30 126 34 124 38 124" {...line} strokeWidth={0.9} opacity={0.6} />
      {/* 手背＋手腕，貼近紙面 */}
      <path d="M80 96 C90 82 112 84 124 98 L172 158 C180 168 176 184 164 192 L148 202 C134 210 116 204 106 192 L78 158 C66 142 68 110 80 96 Z" {...skin} />
      <path d="M106 192 C116 204 134 210 148 202 L160 194 C150 200 132 202 118 190 Z" fill={SKIN_SHADE} />
      <path d="M80 96 C90 82 112 84 124 98 L172 158 C180 168 176 184 164 192 L148 202 C134 210 116 204 106 192 L78 158 C66 142 68 110 80 96 Z" {...line} strokeWidth={2} />
      <path d="M114 112 q10 10 14 20 M126 106 q10 8 16 18" {...line} strokeWidth={0.9} opacity={0.5} />
      {/* 無名指、小指彎在手心下，只露一道弧 */}
      <path d="M80 160 C80 170 88 176 98 174" {...line} strokeWidth={1.3} opacity={0.8} />
      {/* 筆：筆尖碰紙、筆身斜靠在虎口，一點塑膠反光與筆夾 */}
      <path d="M24 142 L124 42" stroke="#2d2b30" strokeWidth={7.5} strokeLinecap="round" />
      <path d="M27 136 L120 43" stroke="#77737b" strokeWidth={1.3} strokeLinecap="round" opacity={0.8} />
      <path d="M12 154 L19 137 L29 147 Z" fill="#b8bcbf" stroke={INK} strokeWidth={1.1} strokeLinejoin="round" />
      <circle cx={PEN_TIP.x} cy={PEN_TIP.y} r={1.6} fill={INK} />
      <path d="M104 58 L112 52 L122 62" fill="none" stroke="#9aa0a4" strokeWidth={2} strokeLinecap="round" />
      {/* 食指：壓在筆上 */}
      <path d="M34 126 C30 120 34 114 40 112 L80 86 C88 80 100 88 96 98 L52 132 C46 136 38 132 34 126 Z" {...skin} />
      <path d="M34 126 C30 120 34 114 40 112 L80 86 C88 80 100 88 96 98 L52 132 C46 136 38 132 34 126 Z" {...line} strokeWidth={1.7} />
      <path d="M36 124 C36 120 38 117 42 116" {...line} strokeWidth={0.9} opacity={0.6} />
      <path d="M60 108 q5 3 6 8 M78 95 q4 3 5 7" {...line} strokeWidth={0.9} opacity={0.55} />
    </svg>
  );
});
