// 手帳上的完成記號：一筆畫的勾勾、幾顆小星星、露坑小印章（磚紅墨）。都是插畫，不是 UI 文字。
import { forwardRef } from "react";

const INK = "#33261c";
const STAMP = "#a9503a";

/** 勾勾（stroke-dasharray 讓它跟著筆尖一筆畫出來）。path 的 id 讓筆可以沿著它走。 */
export const CheckMark = forwardRef<SVGPathElement>(function CheckMark(_props, ref) {
  return (
    <svg className="ledger-check" viewBox="0 0 64 52" aria-hidden="true">
      <path
        ref={ref}
        className="ledger-check-path"
        d="M6 28 C12 32 18 38 22 44 C30 30 42 16 58 6"
        fill="none"
        stroke="#2e6b4a"
        strokeWidth={5.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        filter="url(#cs-rough)"
      />
    </svg>
  );
});

function starPath(size: number): string {
  const pts: string[] = [];
  for (let i = 0; i < 10; i += 1) {
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    const r = i % 2 === 0 ? size : size * 0.45;
    pts.push(`${(Math.cos(a) * r).toFixed(1)} ${(Math.sin(a) * r).toFixed(1)}`);
  }
  return `M${pts.join(" L")} Z`;
}

const STARS = [
  { x: 10, y: 12, s: 7 },
  { x: 58, y: 4, s: 9 },
  { x: 70, y: 40, s: 6 },
];

export function LedgerStars() {
  return (
    <svg className="ledger-stars" viewBox="0 0 80 50" aria-hidden="true">
      {STARS.map((star) => (
        <g key={`${star.x}-${star.y}`} transform={`translate(${star.x} ${star.y})`}>
          <path className="ledger-star" d={starPath(star.s)} fill="#f0c43b" stroke={INK} strokeWidth={1.4} strokeLinejoin="round" />
        </g>
      ))}
    </svg>
  );
}

/** 露坑小印章：雙圈、中間 logo、下緣「已付款」，墨色偏磚紅、帶一點斑駁。 */
export function PaidStamp() {
  return (
    <svg className="ledger-stamp" viewBox="0 0 120 120" aria-hidden="true">
      <defs>
        <mask id="ledger-stamp-mark" maskUnits="userSpaceOnUse" x="34" y="26" width="52" height="44" style={{ maskType: "alpha" }}>
          <image href="/brand/luken-mark.png" x="34" y="26" width="52" height="44" />
        </mask>
      </defs>
      <g filter="url(#cs-rough)" opacity={0.86}>
        <circle cx="60" cy="60" r="54" fill="none" stroke={STAMP} strokeWidth={5} />
        <circle cx="60" cy="60" r="45" fill="none" stroke={STAMP} strokeWidth={1.8} />
        <rect x="34" y="26" width="52" height="44" fill={STAMP} mask="url(#ledger-stamp-mark)" />
        <text x="60" y="90" textAnchor="middle" fill={STAMP} fontSize="17" fontWeight="800" letterSpacing="3">
          已付款
        </text>
      </g>
    </svg>
  );
}
