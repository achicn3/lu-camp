"use client";
// 簽名畫布（docs/23 K3）：pointer 事件（觸控/滑鼠通用）作畫，深色筆跡＋透明背景 →
// canvas.toDataURL('image/png') 輸出 8-bit RGBA（後端唯一允收子集）。固定高解析 backing
// store，顯示尺寸由 CSS 縮放；「放大」切全螢幕覆蓋而不清空筆跡（同一 canvas 元素）。
import { forwardRef, useImperativeHandle, useRef, useState } from "react";

// backing store 解析度（固定，放大/縮小只改顯示尺寸、不重設 canvas → 不清筆跡）。
const CANVAS_W = 1000;
const CANVAS_H = 360;
const STROKE = "#23291f"; // var(--ink) 的實色（canvas 不吃 CSS 變數）
// 客端墨跡門檻：與後端「可見墨跡」判定（alpha≥64 且亮度<200）同語意，門檻取
// 略高於後端的 100（用 200）以確保客端放行者後端必收，不會送出才被 422 打回。
const INK_ALPHA_MIN = 64;
const INK_DARKNESS_MAX = 200;
const MIN_INK_PIXELS = 200;

export interface SignatureCanvasHandle {
  /** 匯出簽名 PNG 的 base64（不含 data: 前綴）；空白時回 null。 */
  toBase64(): string | null;
  clear(): void;
}

export const SignatureCanvas = forwardRef<
  SignatureCanvasHandle,
  { onInkChange: (hasInk: boolean) => void; locked?: boolean }
>(function SignatureCanvas({ onInkChange, locked = false }, ref) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawing = useRef(false);
  const hasInk = useRef(false);
  const last = useRef<{ x: number; y: number } | null>(null);
  const [enlarged, setEnlarged] = useState(false);

  function ctx(): CanvasRenderingContext2D | null {
    return canvasRef.current?.getContext("2d") ?? null;
  }

  function toCanvasCoords(e: React.PointerEvent<HTMLCanvasElement>): { x: number; y: number } {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) / rect.width) * CANVAS_W,
      y: ((e.clientY - rect.top) / rect.height) * CANVAS_H,
    };
  }

  // 以實際墨跡像素數（而非「有畫任何一筆」）判定簽名是否足夠，與後端非空白門檻對齊：
  // 避免「單點/極短一劃」通過客端卻被後端 422（Codex K3 medium）。
  function countInkPixels(): number {
    const c = ctx();
    if (!c) return 0;
    const data = c.getImageData(0, 0, CANVAS_W, CANVAS_H).data;
    let ink = 0;
    for (let i = 0; i < data.length; i += 4) {
      const alpha = data[i + 3];
      const luma = (data[i] + data[i + 1] + data[i + 2]) / 3;
      if (alpha >= INK_ALPHA_MIN && luma < INK_DARKNESS_MAX) ink += 1;
    }
    return ink;
  }

  function refreshInk() {
    const enough = countInkPixels() >= MIN_INK_PIXELS;
    if (enough !== hasInk.current) {
      hasInk.current = enough;
      onInkChange(enough);
    }
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    if (locked) return; // 曖昧提交後鎖定：不得改動簽名（重送須為同一影像）
    e.preventDefault();
    drawing.current = true;
    last.current = toCanvasCoords(e);
    try {
      canvasRef.current?.setPointerCapture(e.pointerId);
    } catch {
      // 某些環境（含合成滑鼠→pointer 事件）不支援 capture；不影響作畫。
    }
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawing.current) return;
    const c = ctx();
    const from = last.current;
    if (!c || !from) return;
    const to = toCanvasCoords(e);
    c.lineWidth = 3.5;
    c.lineCap = "round";
    c.lineJoin = "round";
    c.strokeStyle = STROKE;
    c.beginPath();
    c.moveTo(from.x, from.y);
    c.lineTo(to.x, to.y);
    c.stroke();
    last.current = to;
  }

  function endStroke() {
    if (!drawing.current) return;
    drawing.current = false;
    last.current = null;
    refreshInk(); // 每筆結束才數一次墨跡（避免逐點 getImageData 拖慢作畫）
  }

  function clear() {
    const c = ctx();
    if (c) c.clearRect(0, 0, CANVAS_W, CANVAS_H);
    if (hasInk.current) {
      hasInk.current = false;
      onInkChange(false);
    }
  }

  useImperativeHandle(ref, () => ({
    toBase64() {
      if (!hasInk.current) return null;
      const url = canvasRef.current?.toDataURL("image/png") ?? "";
      const comma = url.indexOf(",");
      return comma >= 0 ? url.slice(comma + 1) : null;
    },
    clear,
  }));

  return (
    <div className={enlarged ? "kiosk-sign-wrap kiosk-sign-wrap--enlarged" : "kiosk-sign-wrap"}>
      <div className="kiosk-sign-toolbar">
        <span className="kiosk-sign-hint">請於下方框內簽名</span>
        <div className="kiosk-sign-actions">
          <button type="button" className="btn-ghost" onClick={() => setEnlarged((v) => !v)}>
            {enlarged ? "縮小" : "放大簽名"}
          </button>
          <button type="button" className="btn-ghost" onClick={clear} disabled={locked}>
            清除重簽
          </button>
        </div>
      </div>
      <canvas
        ref={canvasRef}
        width={CANVAS_W}
        height={CANVAS_H}
        className="kiosk-sign-canvas"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endStroke}
        onPointerLeave={endStroke}
        onPointerCancel={endStroke}
      />
    </div>
  );
});
