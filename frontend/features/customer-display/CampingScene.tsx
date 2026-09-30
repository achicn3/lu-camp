"use client";

import { gsap } from "gsap";
import { useEffect, useRef } from "react";

import { buildDriveHtml, createDriveController } from "./camping/drive-scene";
import { createCampingController, type CampingController, type SceneMode } from "./camping/timeline";
import { buildSceneHtml } from "./camping/world";

export type { SceneMode };

/** 待機沿用無縫車程；結帳與簽畢使用核准的舊版營桌。文件畫面不建立動畫。 */
export function CampingScene({ mode, itemCount = 0 }: { mode: SceneMode; itemCount?: number }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const legacyRef = useRef<CampingController | null>(null);
  const contextRef = useRef<gsap.Context | null>(null);
  const roadTime = useRef(0);
  const lastCount = useRef(itemCount);
  const modeRef = useRef(mode);
  useEffect(() => { modeRef.current = mode; }, [mode]);
  const family = mode === "idle" ? "drive" : mode === "hidden" ? "hidden" : "legacy";

  useEffect(() => {
    const root = rootRef.current;
    if (!root || family === "hidden") return;
    // 無 SVG geometry 的環境（例如 jsdom）只呈現交易文字。
    if (typeof SVGGraphicsElement === "undefined" || typeof SVGGraphicsElement.prototype.getBBox !== "function") return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    if (family === "drive") {
      root.innerHTML = buildDriveHtml();
      const drive = createDriveController(root, reduced);
      drive.seek(roadTime.current);
      return () => {
        roadTime.current = drive.snapshot().time;
        drive.destroy();
        root.innerHTML = "";
      };
    }
    root.innerHTML = buildSceneHtml();
    const context = gsap.context(() => {
      legacyRef.current = createCampingController(root, reduced, modeRef.current);
    }, root);
    contextRef.current = context;
    return () => {
      legacyRef.current?.destroy();
      context.revert();
      legacyRef.current = null;
      contextRef.current = null;
      root.innerHTML = "";
    };
  }, [family]);

  useEffect(() => {
    contextRef.current?.add(() => legacyRef.current?.setMode(mode));
  }, [mode]);
  useEffect(() => {
    if (mode === "cart" && itemCount > lastCount.current) {
      contextRef.current?.add(() => legacyRef.current?.bump());
    }
    lastCount.current = itemCount;
  }, [itemCount, mode]);

  return <div ref={rootRef} className={`camping-scene is-ready ${family === "drive" ? "camping-drive" : ""}`} data-mode={mode} aria-hidden="true" />;
}
