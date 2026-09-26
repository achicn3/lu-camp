"use client";
// 顧客螢幕的手繪露營動畫（店主 2026-09-27 定稿手繪風）。畫面內容在 camping/ 底下：
//   world.ts 組出整個世界、timeline.ts 管待機時間軸與結帳銜接。
// 整個顧客螢幕只掛一份，切換待機／結帳／完成時不重掛，動畫才能順順接下去：
//   idle      待機，一輪約 93 秒循環播放
//   cart      開始結帳：鏡頭從當下畫面帶回營桌拉近，客人坐著喝咖啡
//   celebrate 成交或簽署完成：舉杯＋塗鴉「謝謝光臨」
//   hidden    簽署內容等需要完整閱讀的畫面：暫停並隱藏
// 系統「減少動態效果」時停在泡咖啡那一幕，不播放。
//
// 場景有將近 1MB 的 SVG：等畫面先出來、瀏覽器閒下來才組（不拖慢客顯第一個畫面），
// 而且只在第一次需要露出動畫時才組——一開機就是簽署畫面時完全不花這筆成本。
import { useEffect, useRef, useState } from "react";

import { type CampingController, type SceneMode, createCampingController } from "./camping/timeline";
import { buildSceneHtml } from "./camping/world";

export type { SceneMode };

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** 瀏覽器量得到 SVG 尺寸才跑動畫（測試用的 jsdom 沒有 getBBox）。 */
function canAnimate(): boolean {
  return typeof SVGGraphicsElement !== "undefined" && typeof SVGGraphicsElement.prototype.getBBox === "function";
}

function whenIdle(run: () => void): () => void {
  if (typeof window.requestIdleCallback === "function") {
    const id = window.requestIdleCallback(run, { timeout: 800 });
    return () => window.cancelIdleCallback(id);
  }
  const id = window.setTimeout(run, 50);
  return () => window.clearTimeout(id);
}

export function CampingScene({ mode, itemCount = 0 }: { mode: SceneMode; itemCount?: number }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<CampingController | null>(null);
  const modeRef = useRef(mode);
  const lastCount = useRef(itemCount);
  const [wanted, setWanted] = useState(mode !== "hidden");
  const [ready, setReady] = useState(false);
  // 第一次要露出動畫時才組場景（render 中依 prop 調整 state，React 建議的寫法）
  if (mode !== "hidden" && !wanted) setWanted(true);

  useEffect(() => {
    modeRef.current = mode;
    controllerRef.current?.setMode(mode);
  }, [mode]);

  useEffect(() => {
    const root = rootRef.current;
    if (!wanted || !root || !canAnimate()) return;
    let controller: CampingController | null = null;
    const cancel = whenIdle(() => {
      root.innerHTML = buildSceneHtml();
      controller = createCampingController(root, prefersReducedMotion(), modeRef.current);
      controllerRef.current = controller;
      setReady(true);
    });
    return () => {
      cancel();
      controller?.destroy();
      controllerRef.current = null;
      root.innerHTML = "";
    };
  }, [wanted]);

  useEffect(() => {
    if (itemCount > lastCount.current) controllerRef.current?.bump();
    lastCount.current = itemCount;
  }, [itemCount]);

  return <div ref={rootRef} className={ready ? "camping-scene is-ready" : "camping-scene"} data-mode={mode} aria-hidden="true" />;
}
