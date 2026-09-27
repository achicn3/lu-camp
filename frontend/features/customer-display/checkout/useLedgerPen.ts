// 結帳手帳的筆：每掃一件商品，手移到那一行、筆尖碰紙、快速寫幾下，文字跟著筆尖從左到右出現。
// 店主 2026-09-27：
// - 一筆約 0.4～0.8 秒；連續快速掃描時上一筆立刻寫完、下一筆縮到 0.25～0.35 秒，動畫不排隊。
// - 同品項數量增加：不新增一行，手移到數量改一筆；刪除：在那行劃一條線再收起來。
// - 付款：筆放到紙旁；處理中輕敲；完成：沿著勾勾一筆畫、星星、蓋章。
// 只用 transform／clip-path／stroke-dashoffset，不每幀重畫手。
import { gsap } from "gsap";
import { type RefObject, useEffect, useLayoutEffect, useRef } from "react";

import { PEN_TIP } from "./PenHand";

export type LedgerLine = { key: string; qty: number; amount: string };
export type LedgerPhase = "writing" | "paying" | "processing" | "paid";

/** 兩筆間隔小於這個毫秒數就算「連續快速掃描」。 */
const RAPID_MS = 700;

type Refs = {
  shell: RefObject<HTMLElement | null>;
  hand: RefObject<SVGSVGElement | null>;
  check: RefObject<SVGPathElement | null>;
};

/** 不播動畫時直接把手擺到定位（不經過 GSAP：測試環境量不到 SVG 尺寸）。 */
function snap(hand: SVGSVGElement, x: number, y: number, rotation = 0): void {
  hand.setAttribute("style", `transform: translate(${x - PEN_TIP.x}px, ${y - PEN_TIP.y}px) rotate(${rotation}deg)`);
}

/** 不管動畫是寫完還是被打斷，都不能讓任何一行卡在半遮住的狀態。 */
function clearClips(shell: Element | null): void {
  shell?.querySelectorAll<HTMLElement>("[data-ledger-key], [data-ledger-key] .kiosk-cart-qty, [data-ledger-key] > strong").forEach((el) => {
    el.style.clipPath = "";
  });
}

/**
 * 元素相對結帳畫面的位置。用版面位置（offsetLeft/Top）而不是畫面上的位置：夾板剛滑進來、付款時上移都是
 * transform，量畫面位置會量到動畫途中的座標，手就擺錯地方。捲動中的明細要扣掉捲動量。
 */
function rectIn(el: Element, shell: Element): DOMRect {
  let x = 0;
  let y = 0;
  let node: HTMLElement | null = el as HTMLElement;
  while (node && node !== shell) {
    x += node.offsetLeft;
    y += node.offsetTop;
    const parent: HTMLElement | null = node.offsetParent as HTMLElement | null;
    for (let scroller: HTMLElement | null = node.parentElement; scroller && scroller !== parent; scroller = scroller.parentElement) {
      x -= scroller.scrollLeft;
      y -= scroller.scrollTop;
    }
    node = parent;
  }
  const box = el as HTMLElement;
  return new DOMRect(x, y, box.offsetWidth, box.offsetHeight);
}

export function useLedgerPen(refs: Refs, lines: LedgerLine[], phase: LedgerPhase, motion: boolean): void {
  const prev = useRef<Map<string, LedgerLine> | null>(null);
  const current = useRef<gsap.core.Timeline | null>(null);
  const idle = useRef<gsap.core.Animation | null>(null);
  const lastAt = useRef(0);
  const signature = lines.map((l) => `${l.key}:${l.qty}:${l.amount}`).join("|");

  /** 筆的「休息位置」：紙的左下角（總額在右邊，手不要擋到）。 */
  const restPoint = (): { x: number; y: number } => {
    const shell = refs.shell.current;
    const paper = shell?.querySelector(".ledger-paper");
    if (!shell || !paper) return { x: 0, y: 0 };
    const p = rectIn(paper, shell);
    return { x: p.left + 120, y: p.bottom - 175 };
  };

  const place = (tl: gsap.core.Timeline, x: number, y: number, duration: number, pos?: gsap.Position, rotation = 0) =>
    tl.to(refs.hand.current, { x: x - PEN_TIP.x, y: y - PEN_TIP.y, rotation, duration, ease: "power2.out" }, pos);

  // 手第一次出現：從右下角滑進來停在休息位置
  useEffect(() => {
    const hand = refs.hand.current;
    if (!hand) return;
    const rest = restPoint();
    if (!motion) {
      snap(hand, rest.x, rest.y);
      return;
    }
    gsap.fromTo(
      hand,
      { x: rest.x - PEN_TIP.x + 160, y: rest.y - PEN_TIP.y + 220, rotation: 8, transformOrigin: `${PEN_TIP.x}px ${PEN_TIP.y}px` },
      { x: rest.x - PEN_TIP.x, y: rest.y - PEN_TIP.y, rotation: 0, duration: 0.7, delay: 0.35, ease: "power2.out" },
    );
    return () => {
      current.current?.progress(1).kill();
      idle.current?.kill();
      gsap.killTweensOf(hand);
      clearClips(refs.shell.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在掛上時跑一次
  }, []);

  // 商品變動 → 寫字
  useLayoutEffect(() => {
    const shell = refs.shell.current;
    const hand = refs.hand.current;
    const before = prev.current;
    const now = new Map(lines.map((l) => [l.key, l]));
    prev.current = now;
    if (!motion || !shell || !hand || phase !== "writing") return;

    const added = before === null ? (lines.length <= 3 ? lines.slice(-1) : []) : lines.filter((l) => !before.has(l.key));
    const amended = before === null ? [] : lines.filter((l) => before.has(l.key) && before.get(l.key)?.qty !== l.qty);
    const removed = before === null ? [] : [...before.keys()].filter((k) => !now.has(k));
    if (added.length + amended.length + removed.length === 0) return;

    // 還在寫上一筆：直接寫完，手接著去下一行
    const t = performance.now();
    // 連續掃描：距上一筆很近、上一筆還在寫、或這次一口氣進來好幾件，都用快速版
    const rapid = t - lastAt.current < RAPID_MS || current.current?.isActive() === true || added.length + amended.length > 1;
    lastAt.current = t;
    current.current?.progress(1).kill();
    idle.current?.kill();
    clearClips(shell);

    // 剛出現的第一件：等手從右下角滑進來再寫
    const tl = gsap.timeline({ delay: before === null ? 0.9 : 0, onComplete: () => clearClips(shell), onInterrupt: () => clearClips(shell) });
    const move = rapid ? 0.12 : 0.22;
    const write = rapid ? 0.22 : 0.42;
    const rowOf = (key: string) => shell.querySelector<HTMLElement>(`[data-ledger-key="${CSS.escape(key)}"]`);

    const scribble = (fromX: number, toX: number, y: number, duration: number) => {
      place(tl, fromX, y, move);
      tl.to(hand, { x: toX - PEN_TIP.x, duration, ease: "none" });
      const strokes = rapid ? 2 : 3 + Math.floor(Math.random() * 2);
      tl.to(hand, { y: `-=${4 + Math.random() * 2}`, rotation: -3, duration: duration / (strokes * 2), yoyo: true, repeat: strokes * 2 - 1, ease: "sine.inOut" }, "<");
    };

    for (const line of added) {
      const row = rowOf(line.key);
      if (!row) continue;
      row.scrollIntoView({ block: "nearest" });
      const r = rectIn(row, shell);
      row.style.clipPath = "inset(0 100% 0 0)";
      const start = tl.duration() + move;
      scribble(r.left + 6, r.right - 10, r.top + r.height * 0.45, write);
      tl.fromTo(row, { clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0% 0 0)", duration: write, ease: "none", clearProps: "clipPath" }, start);
    }
    for (const line of amended) {
      const row = rowOf(line.key);
      const qty = row?.querySelector<HTMLElement>(".kiosk-cart-qty");
      const amount = row?.querySelector<HTMLElement>(":scope > strong");
      if (!row || !qty) continue;
      row.scrollIntoView({ block: "nearest" });
      const q = rectIn(qty, shell);
      const targets = amount ? [qty, amount] : [qty];
      for (const el of targets) el.style.clipPath = "inset(0 100% 0 0)";
      const start = tl.duration() + move;
      const end = amount ? rectIn(amount, shell).right : q.right;
      scribble(q.left, end, q.top + q.height * 0.5, write * 0.7);
      tl.fromTo(targets, { clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0% 0 0)", duration: write * 0.7, ease: "none", clearProps: "clipPath" }, start);
    }
    for (const key of removed) {
      const ghost = shell.querySelector<HTMLElement>(`[data-ledger-ghost="${CSS.escape(key)}"]`);
      if (!ghost) continue;
      const r = rectIn(ghost, shell);
      place(tl, r.left + 4, r.top + r.height * 0.5, move);
      tl.to(hand, { x: r.right - 8 - PEN_TIP.x, duration: 0.22, ease: "power1.in" });
    }
    const rest = restPoint();
    place(tl, rest.x, rest.y, 0.35, "+=0.05");
    current.current = tl;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 用 signature 代表商品內容變了
  }, [signature, motion, phase]);

  // 付款各階段
  useEffect(() => {
    const shell = refs.shell.current;
    const hand = refs.hand.current;
    if (!shell || !hand) return;
    const paper = shell.querySelector(".ledger-paper");
    if (!paper) return;
    idle.current?.kill();
    if (phase === "writing") return;
    current.current?.progress(1).kill();
    const p = rectIn(paper, shell);
    // 筆放在紙的右側中段：不要蓋到總額
    const aside = { x: p.right - 120, y: p.top + p.height * 0.4 };
    if (!motion) {
      snap(hand, aside.x, aside.y, 18);
      return;
    }
    const tl = gsap.timeline();
    if (phase === "paying" || phase === "processing") {
      // 筆放到紙旁邊；處理中每隔一下輕敲一次紙
      place(tl, aside.x, aside.y, 0.5, 0, 18);
      if (phase === "processing") {
        idle.current = gsap
          .timeline({ repeat: -1, repeatDelay: 1.3, delay: 0.9 })
          .to(hand, { rotation: 11, duration: 0.14, ease: "sine.inOut" })
          .to(hand, { rotation: 18, duration: 0.18, ease: "sine.inOut" });
      }
    } else if (phase === "paid") {
      // 拿起筆，沿著總額旁的勾勾一筆畫完
      const path = refs.check.current;
      if (path) {
        const len = path.getTotalLength();
        const matrix = path.getScreenCTM();
        const s = shell.getBoundingClientRect();
        const pointAt = (t: number) => {
          const pt = path.getPointAtLength(len * t);
          const x = matrix ? matrix.a * pt.x + matrix.c * pt.y + matrix.e : pt.x;
          const y = matrix ? matrix.b * pt.x + matrix.d * pt.y + matrix.f : pt.y;
          return { x: x - s.left, y: y - s.top };
        };
        const first = pointAt(0);
        gsap.set(path, { strokeDasharray: len, strokeDashoffset: len });
        place(tl, first.x, first.y, 0.35, 0);
        const proxy = { t: 0 };
        tl.to(proxy, {
          t: 1,
          duration: 0.45,
          ease: "power1.inOut",
          onUpdate: () => {
            const pt = pointAt(proxy.t);
            gsap.set(hand, { x: pt.x - PEN_TIP.x, y: pt.y - PEN_TIP.y });
            gsap.set(path, { strokeDashoffset: len * (1 - proxy.t) });
          },
        });
        tl.fromTo(path.ownerSVGElement, { scale: 1 }, { scale: 1.12, duration: 0.12, yoyo: true, repeat: 1, transformOrigin: "50% 50%" });
      }
      tl.fromTo(shell.querySelectorAll(".ledger-star"), { scale: 0, rotation: -90, transformOrigin: "50% 50%" }, { scale: 1, rotation: 0, duration: 0.4, stagger: 0.08, ease: "back.out(2.4)" }, "-=0.1");
      const stamp = shell.querySelector(".ledger-stamp");
      if (stamp) {
        tl.fromTo(stamp, { scale: 1.5, opacity: 0, rotation: -2 }, { scale: 1, opacity: 1, rotation: -10, duration: 0.22, ease: "power3.in" }, "-=0.15");
        tl.fromTo(paper, { y: 0 }, { y: 3, duration: 0.06, yoyo: true, repeat: 1 });
      }
      place(tl, aside.x, aside.y, 0.4, "+=0.05", 18);
    }
    current.current = tl;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只看付款階段變化
  }, [phase, motion]);
}
