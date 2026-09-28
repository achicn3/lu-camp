"use client";

import { useEffect, useRef, useState } from "react";
import { createFilmController, type FilmController } from "./film/controller";
import type { SceneMode } from "./film/state";
export type { SceneMode };

/** 客顯共用一張畫布；文件期間隱藏，動畫永遠不承擔交易成功的判定。 */
export function CampingScene({ mode, itemCount = 0 }: { mode: SceneMode; itemCount?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controller = useRef<FilmController | null>(null);
  const latest = useRef({ mode, itemCount });
  const [ready, setReady] = useState(false);
  useEffect(() => {
    latest.current = { mode, itemCount };
    controller.current?.setMode(mode);
    controller.current?.setItemCount(itemCount);
  }, [mode, itemCount]);
  useEffect(() => {
    const canvas = canvasRef.current;
    // jsdom 不提供實際畫布；瀏覽器視覺行為由 Playwright 驗證。
    if (!canvas || typeof CanvasRenderingContext2D === "undefined") return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const init = () => {
      controller.current?.destroy();
      controller.current = createFilmController(canvas, media.matches, latest.current.mode, latest.current.itemCount);
      setReady(true);
    };
    init(); media.addEventListener("change", init);
    return () => { media.removeEventListener("change", init); controller.current?.destroy(); controller.current = null; };
  }, []);
  return <div className={`camping-scene camp-film${ready ? " is-ready" : ""}`} data-mode={mode} aria-hidden="true"><canvas ref={canvasRef} /></div>;
}
