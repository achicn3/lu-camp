"use client";
// 簽署完成／交易完成的手繪勾勾（店主 2026-09-27：簽署完畢要有動畫）：
// 先畫一圈塗鴉圓、再一筆畫出勾勾，旁邊蹦出三顆小星星。減少動態效果時直接顯示畫好的樣子。
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { useRef } from "react";

gsap.registerPlugin(useGSAP);

const RING = "M50 8 C76 6 94 26 92 52 C90 78 70 94 46 92 C22 90 6 70 8 46 C10 24 28 10 54 9";
const CHECK = "M28 50 L44 66 L74 32";
const STARS = [
  { x: 96, y: 14, s: 9 },
  { x: 4, y: 20, s: 7 },
  { x: 98, y: 80, s: 6 },
];

function starPath(size: number): string {
  const pts: string[] = [];
  for (let i = 0; i < 10; i += 1) {
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    const r = i % 2 === 0 ? size : size * 0.45;
    pts.push(`${(Math.cos(a) * r).toFixed(1)} ${(Math.sin(a) * r).toFixed(1)}`);
  }
  return `M${pts.join(" L")} Z`;
}

export function DoodleCheck() {
  const root = useRef<SVGSVGElement>(null);
  useGSAP(
    () => {
      const reduced =
        typeof window.matchMedia !== "function" ||
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (reduced) return;
      const tl = gsap.timeline();
      tl.fromTo(".doodle-ring", { strokeDashoffset: 300 }, { strokeDashoffset: 0, duration: 0.7, ease: "power2.out" });
      tl.fromTo(".doodle-tick", { strokeDashoffset: 90 }, { strokeDashoffset: 0, duration: 0.45, ease: "power3.out" }, "-=0.15");
      tl.fromTo(
        ".doodle-star",
        { scale: 0, rotation: -90, transformOrigin: "50% 50%" },
        { scale: 1, rotation: 0, duration: 0.5, stagger: 0.1, ease: "back.out(2.6)" },
        "-=0.2",
      );
    },
    { scope: root },
  );
  return (
    <svg ref={root} className="kiosk-doodle-check" viewBox="-6 -6 112 112" aria-hidden="true">
      <circle cx="50" cy="50" r="42" fill="#dfeccf" />
      <path
        className="doodle-ring"
        d={RING}
        fill="none"
        stroke="#2a2018"
        strokeWidth="4"
        strokeLinecap="round"
        strokeDasharray="300"
      />
      <path
        className="doodle-tick"
        d={CHECK}
        fill="none"
        stroke="#2e7d4f"
        strokeWidth="9"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray="90"
      />
      {STARS.map((star) => (
        <g key={`${star.x}-${star.y}`} transform={`translate(${star.x} ${star.y})`}>
          <path
            className="doodle-star"
            d={starPath(star.s)}
            fill="#f0c43b"
            stroke="#2a2018"
            strokeWidth="1.6"
            strokeLinejoin="round"
          />
        </g>
      ))}
    </svg>
  );
}
