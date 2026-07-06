"use client";
// 簽名畫布（docs/23 K3）：pointer 事件（觸控/滑鼠通用）作畫，深色筆跡＋透明背景 →
// canvas.toDataURL('image/png') 輸出 8-bit RGBA（後端唯一允收子集）。固定高解析 backing
// store，顯示尺寸由 CSS 縮放；「放大」切全螢幕覆蓋而不清空筆跡（同一 canvas 元素）。
import { forwardRef, useImperativeHandle, useRef, useState } from "react";

// backing store 解析度（固定，放大/縮小只改顯示尺寸、不重設 canvas → 不清筆跡）。
const CANVAS_W = 1000;
const CANVAS_H = 360;
const STROKE = "#23291f"; // var(--ink) 的實色（canvas 不吃 CSS 變數）

export interface SignatureCanvasHandle {
  /** 匯出簽名 PNG 的 base64（不含 data: 前綴）；空白時回 null。 */
  toBase64(): string | null;
  clear(): void;
}

export const SignatureCanvas = forwardRef<
  SignatureCanvasHandle,
  { onInkChange: (hasInk: boolean) => void }
>(function SignatureCanvas({ onInkChange }, ref) {
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

  function markInk() {
    if (!hasInk.current) {
      hasInk.current = true;
      onInkChange(true);
    }
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    e.preventDefault();
    drawing.current = true;
    const at = toCanvasCoords(e);
    last.current = at;
    try {
      canvasRef.current?.setPointerCapture(e.pointerId);
    } catch {
      // 某些環境（含合成滑鼠→pointer 事件）不支援 capture；不影響作畫。
    }
    // 落點即點一個小點：即使只是輕點也留下墨跡（作畫由 move 續接）。
    const c = ctx();
    if (c) {
      c.fillStyle = STROKE;
      c.beginPath();
      c.arc(at.x, at.y, 2, 0, Math.PI * 2);
      c.fill();
    }
    markInk();
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
    markInk();
  }

  function endStroke() {
    drawing.current = false;
    last.current = null;
  }

  function clear() {
    const c = ctx();
    if (c) c.clearRect(0, 0, CANVAS_W, CANVAS_H);
    hasInk.current = false;
    onInkChange(false);
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
          <button type="button" className="btn-ghost" onClick={clear}>
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
