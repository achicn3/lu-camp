// 結帳手帳的筆（店主 2026-09-27 定稿規格 G–L、S、AA）：
// - 新增：手移到那一行、筆尖落下、寫 2～4 下，文字跟著筆尖出現，約 0.55 秒。
// - 連續掃描（0.5 秒內又來一件、或上一筆還在寫、或一次來好幾件）：上一筆立刻寫完，每筆約 0.25 秒；
//   POS 資料永遠先到畫面，動畫不排隊。
// - 同品項數量變了：手移到數量，舊數字淡掉、新數字寫上，不新增一行。
// - 刪除：手移過去、斜斜劃一條、再補一條短的，那行變淡停一下，才收起來；總額由畫面在收起後才更新。
// - 付款：筆放在紙上；成功：沿著總額旁的勾勾一筆畫、幾顆星、蓋章。
// 所有動畫都能被下一個 POS 事件打斷（打斷＝直接跳到結尾），只用 transform／opacity／clip-path／SVG 描線。
import { gsap } from "gsap";
import { type RefObject, useEffect, useLayoutEffect, useRef } from "react";

import type { PenActivity } from "./ledgerState";
import { PEN_TIP } from "./PenHand";

export type LedgerLine = { key: string; qty: number; amount: string };
export type LedgerPhase = "writing" | "paying" | "paid" | "failed";

/** 兩筆間隔小於這個毫秒數就算連續快速掃描。 */
const RAPID_MS = 500;

type Refs = {
  shell: RefObject<HTMLElement | null>;
  hand: RefObject<HTMLImageElement | null>;
  check: RefObject<SVGPathElement | null>;
};

type Callbacks = {
  /** 刪除那行已經收起來了（畫面可以拿掉它、更新總額）。 */
  onGhostDone(key: string): void;
  /** 筆正在做什麼（給狀態顯示用）。 */
  onPen?(activity: PenActivity): void;
};

/** 不播動畫時直接把手擺到定位（不經過 GSAP：測試環境量不到 SVG 尺寸）。 */
function snap(hand: HTMLElement, x: number, y: number, rotation = 0): void {
  hand.setAttribute("style", `transform: translate(${x - PEN_TIP.x}px, ${y - PEN_TIP.y}px) rotate(${rotation}deg)`);
}

/** 不管動畫是寫完還是被打斷，都不能讓任何一行卡在半遮住的狀態、或留下舊數字的副本。 */
function clearMarks(shell: Element | null): void {
  shell?.querySelectorAll<HTMLElement>("[data-ledger-key], [data-ledger-key] .kiosk-cart-qty, [data-ledger-key] > strong").forEach((el) => {
    el.style.clipPath = "";
  });
  shell?.querySelectorAll(".ledger-old-qty").forEach((el) => el.remove());
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

export function useLedgerPen(refs: Refs, lines: LedgerLine[], phase: LedgerPhase, motion: boolean, callbacks: Callbacks): void {
  const prev = useRef<Map<string, LedgerLine> | null>(null);
  const current = useRef<gsap.core.Timeline | null>(null);
  const lastAt = useRef(0);
  const cb = useRef(callbacks);
  useLayoutEffect(() => {
    cb.current = callbacks;
  });
  const signature = lines.map((l) => `${l.key}:${l.qty}:${l.amount}`).join("|");

  /** 筆的休息位置：紙的左下角（總額在右邊，手不要擋到），筆尖落在紙上。 */
  const restPoint = (): { x: number; y: number } => {
    const shell = refs.shell.current;
    const paper = shell?.querySelector(".ledger-paper");
    if (!shell || !paper) return { x: 0, y: 0 };
    const p = rectIn(paper, shell);
    return { x: p.left + 60, y: p.bottom - 40 };
  };
  /** 付款時筆放的位置：紙的右側中段（不要蓋到總額）。 */
  const asidePoint = (): { x: number; y: number } => {
    const shell = refs.shell.current;
    const paper = shell?.querySelector(".ledger-paper");
    if (!shell || !paper) return { x: 0, y: 0 };
    const p = rectIn(paper, shell);
    return { x: p.right - 210, y: p.top + p.height * 0.5 };
  };

  const moveTo = (tl: gsap.core.Timeline, x: number, y: number, duration: number, pos?: gsap.Position) =>
    tl.to(refs.hand.current, { x: x - PEN_TIP.x, y: y - PEN_TIP.y, rotation: 0, duration, ease: "power2.out" }, pos);

  /** 打斷目前的動畫：直接跳到結尾（刪除的行會收起、文字都顯示完整）。 */
  const finishCurrent = () => {
    current.current?.progress(1).kill();
    current.current = null;
    clearMarks(refs.shell.current);
  };

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
      { x: rest.x - PEN_TIP.x + 150, y: rest.y - PEN_TIP.y + 200, transformOrigin: `${PEN_TIP.x}px ${PEN_TIP.y}px` },
      { x: rest.x - PEN_TIP.x, y: rest.y - PEN_TIP.y, duration: 0.6, delay: 0.3, ease: "power2.out" },
    );
    return () => {
      finishCurrent();
      gsap.killTweensOf(hand);
      // 重新掛上時（開發模式會掛兩次）要重新認得「第一件」，不然第一件永遠不會寫
      prev.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在掛上時跑一次
  }, []);

  // 商品變動 → 寫字／改數量／劃掉
  useLayoutEffect(() => {
    const shell = refs.shell.current;
    const hand = refs.hand.current;
    const before = prev.current;
    const now = new Map(lines.map((l) => [l.key, l]));
    prev.current = now;
    if (!shell || !hand || phase !== "writing") return;
    const ghosts = [...shell.querySelectorAll<HTMLElement>("[data-ledger-ghost]")].map((el) => el.dataset.ledgerGhost ?? "");
    const added = before === null ? (lines.length <= 3 ? lines.slice(-1) : []) : lines.filter((l) => !before.has(l.key) && !ghosts.includes(l.key));
    const amended = before === null ? [] : lines.filter((l) => before.has(l.key) && before.get(l.key)?.qty !== l.qty);
    const removed = before === null ? [] : [...before.keys()].filter((k) => !now.has(k));
    if (added.length + amended.length + removed.length === 0) return;
    if (!motion) {
      // 減少動態效果：資料直接更新，刪除的行由畫面快速淡出
      for (const key of removed) window.setTimeout(() => cb.current.onGhostDone(key), 180);
      return;
    }

    const t = performance.now();
    const rapid = t - lastAt.current < RAPID_MS || current.current?.isActive() === true || added.length + amended.length > 1;
    lastAt.current = t;
    finishCurrent();

    const tl = gsap.timeline({
      // 剛出現的第一件：等手從右下角滑進來再寫
      delay: before === null ? 0.8 : 0,
      onComplete: () => {
        clearMarks(shell);
        cb.current.onPen?.(null);
      },
      onInterrupt: () => clearMarks(shell),
    });
    const move = rapid ? 0.07 : 0.18;
    const write = rapid ? 0.15 : 0.34;
    const rowOf = (key: string) => shell.querySelector<HTMLElement>(`[data-ledger-key="${CSS.escape(key)}"]`);
    const writeAcross = (fromX: number, toX: number, y: number, duration: number) => {
      moveTo(tl, fromX, y, move);
      tl.to(hand, { x: toX - PEN_TIP.x, duration, ease: "none" });
      const strokes = rapid ? 2 : 3 + Math.floor(Math.random() * 2);
      tl.to(hand, { y: `-=${3 + Math.random() * 2}`, duration: duration / (strokes * 2), yoyo: true, repeat: strokes * 2 - 1, ease: "sine.inOut" }, "<");
    };

    for (const line of added) {
      const row = rowOf(line.key);
      if (!row) continue;
      row.scrollIntoView({ block: "nearest" });
      const r = rectIn(row, shell);
      row.style.clipPath = "inset(0 100% 0 0)";
      tl.call(() => cb.current.onPen?.("ITEM_ADD"));
      const start = tl.duration() + move;
      writeAcross(r.left + 4, r.right - 8, r.top + r.height * 0.42, write);
      tl.fromTo(row, { clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0% 0 0)", duration: write, ease: "none", clearProps: "clipPath" }, start);
      tl.to(hand, { y: "+=10", duration: 0.08, ease: "power1.out" });
    }

    for (const line of amended) {
      const row = rowOf(line.key);
      const qty = row?.querySelector<HTMLElement>(".kiosk-cart-qty");
      const amount = row?.querySelector<HTMLElement>(":scope > strong");
      const old = before?.get(line.key)?.qty;
      if (!row || !qty) continue;
      row.scrollIntoView({ block: "nearest" });
      const q = rectIn(qty, shell);
      // 舊數字：疊一個淡出的副本在原位（淡完就拿掉；被打斷時 clearMarks 也會拿掉）
      const oldQty = document.createElement("span");
      oldQty.className = "ledger-old-qty";
      oldQty.textContent = `× ${old ?? ""}`;
      oldQty.style.left = `${qty.offsetLeft}px`;
      oldQty.style.top = `${qty.offsetTop}px`;
      row.appendChild(oldQty);
      const targets = amount ? [qty, amount] : [qty];
      for (const el of targets) el.style.clipPath = "inset(0 100% 0 0)";
      tl.call(() => cb.current.onPen?.("ITEM_UPDATE"));
      moveTo(tl, q.left - 2, q.top + q.height * 0.55, move);
      tl.to(oldQty, { opacity: 0, duration: 0.12, onComplete: () => oldQty.remove() });
      const start = tl.duration() - 0.04;
      const end = amount ? rectIn(amount, shell).right : q.right;
      tl.to(hand, { x: end - PEN_TIP.x, duration: write * 0.7, ease: "none" }, start);
      tl.to(hand, { y: "-=4", duration: (write * 0.7) / 4, yoyo: true, repeat: 3, ease: "sine.inOut" }, start);
      tl.fromTo(targets, { clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0% 0 0)", duration: write * 0.7, ease: "none", clearProps: "clipPath" }, start);
    }

    for (const key of removed) {
      const ghost = shell.querySelector<HTMLElement>(`[data-ledger-ghost="${CSS.escape(key)}"]`);
      if (!ghost) {
        cb.current.onGhostDone(key);
        continue;
      }
      const r = rectIn(ghost, shell);
      const strike = ghost.querySelector<SVGPathElement>(".ledger-strike-1");
      const strikeSoft = ghost.querySelector<SVGPathElement>(".ledger-strike-2");
      // 像鉛筆隨手塗掉：在品名那一段來回畫 4～5 個鋸齒，最後拖一條尾巴（店主 2026-09-27 提供的效果圖）
      const w = r.width;
      const hgt = r.height;
      const x0 = 0.2 * w;
      const x1 = 0.56 * w;
      const yMid = 0.36 * hgt;
      const amp = Math.min(14, hgt * 0.17);
      const teeth = 5 + Math.floor(Math.random() * 2);
      const zigEnd = x0 + (x1 - x0) * 0.62;
      const step = (zigEnd - x0) / teeth;
      const j = () => (Math.random() - 0.5) * 2.4;
      // 鋸齒：每一下往右上、再往回拉一點到右下（像手快速來回塗），最後拖一條往右的尾巴
      let d = `M${x0} ${yMid + amp}`;
      for (let k = 0; k < teeth; k += 1) {
        d += ` L${(x0 + step * (k + 0.95) + j()).toFixed(1)} ${(yMid - amp + j()).toFixed(1)}`;
        d += ` L${(x0 + step * (k + 0.6) + j()).toFixed(1)} ${(yMid + amp + j()).toFixed(1)}`;
      }
      const tailStart = x0 + step * (teeth - 0.4);
      d += ` C${tailStart + 30} ${yMid + amp * 0.2} ${x1 - 40} ${yMid - amp * 0.2} ${x1} ${yMid - amp * 0.35}`;
      const svg = strike?.ownerSVGElement;
      svg?.setAttribute("viewBox", `0 0 ${w} ${hgt}`);
      let len = 0;
      for (const path of [strike, strikeSoft]) {
        if (!path) continue;
        path.setAttribute("d", d);
        len = path.getTotalLength();
        path.setAttribute("stroke-dasharray", `${len}`);
        path.setAttribute("stroke-dashoffset", `${len}`);
      }
      tl.call(() => cb.current.onPen?.("ITEM_DELETE"));
      const start = strike ? strike.getPointAtLength(0) : { x: x0, y: yMid };
      moveTo(tl, r.left + start.x, r.top + start.y, rapid ? 0.12 : 0.22);
      if (strike) {
        const proxy = { t: 0 };
        tl.to(proxy, {
          t: 1,
          duration: rapid ? 0.24 : 0.5,
          ease: "power1.inOut",
          onUpdate: () => {
            const pt = strike.getPointAtLength(len * proxy.t);
            gsap.set(hand, { x: r.left + pt.x - PEN_TIP.x, y: r.top + pt.y - PEN_TIP.y });
            for (const path of [strike, strikeSoft]) path?.setAttribute("stroke-dashoffset", `${len * (1 - proxy.t)}`);
          },
        });
      }
      // 字變淡到一半，劃線維持深色
      tl.to(ghost.querySelectorAll(":scope > :not(.ledger-strike)"), { opacity: 0.48, duration: 0.15 });
      // 停一下讓客人看到真的被劃掉，再收起來、下面的行往上補
      tl.to(ghost, { height: 0, paddingTop: 0, paddingBottom: 0, opacity: 0, duration: 0.3, ease: "power2.inOut" }, rapid ? "+=0.2" : "+=0.45");
      tl.call(() => cb.current.onGhostDone(key));
    }

    const rest = restPoint();
    moveTo(tl, rest.x, rest.y, 0.3, "+=0.05");
    current.current = tl;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 用 signature 代表商品內容變了
  }, [signature, motion, phase]);

  // 付款各階段（一進入付款就立刻結束還在寫的商品動畫）
  useEffect(() => {
    const shell = refs.shell.current;
    const hand = refs.hand.current;
    if (!shell || !hand) return;
    const paper = shell.querySelector<HTMLElement>(".ledger-paper");
    if (!paper || phase === "writing" || phase === "failed") return;
    finishCurrent();
    const aside = asidePoint();
    if (!motion) {
      snap(hand, aside.x, aside.y);
      return;
    }
    const tl = gsap.timeline();
    if (phase === "paying") {
      moveTo(tl, aside.x, aside.y, 0.45, 0);
    } else {
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
        moveTo(tl, first.x, first.y, 0.32, 0);
        const proxy = { t: 0 };
        tl.to(proxy, {
          t: 1,
          duration: 0.42,
          ease: "power1.inOut",
          onUpdate: () => {
            const pt = pointAt(proxy.t);
            gsap.set(hand, { x: pt.x - PEN_TIP.x, y: pt.y - PEN_TIP.y });
            gsap.set(path, { strokeDashoffset: len * (1 - proxy.t) });
          },
        });
        tl.to(hand, { y: "+=14", x: "+=10", duration: 0.12, ease: "power1.out" });
        tl.fromTo(path.ownerSVGElement, { scale: 1 }, { scale: 1.08, duration: 0.1, yoyo: true, repeat: 1, transformOrigin: "50% 50%" }, "<");
      }
      tl.fromTo(shell.querySelectorAll(".ledger-star"), { scale: 0, transformOrigin: "50% 50%" }, { scale: 1, duration: 0.3, stagger: 0.07, ease: "back.out(1.8)" }, "-=0.05");
      const stamp = shell.querySelector(".ledger-stamp");
      if (stamp) {
        tl.fromTo(stamp, { scale: 1.06, opacity: 0 }, { scale: 1, opacity: 1, duration: 0.16, ease: "power2.in" }, "+=0.05");
        tl.fromTo(paper, { y: 0 }, { y: 1.5, duration: 0.05, yoyo: true, repeat: 1 });
      }
      moveTo(tl, aside.x, aside.y, 0.4, "+=0.05");
    }
    current.current = tl;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只看付款階段變化
  }, [phase, motion]);
}
