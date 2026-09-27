// 手繪風 SVG 的小工具：全部回傳字串，由 CampingScene 一次塞進 DOM。
// 畫風（店主 2026-09-27 定稿的樣張）：紙紋底、鉛筆上色（pencil 濾鏡）、抖動墨線（rough 濾鏡）、
// 斜線陰影。亂數一律用固定種子，每次開機畫出來都一樣。
import { MARKER_GLYPHS, ROUND_GLYPHS, type Glyph } from "./glyphs";

/** 外輪廓：深炭棕（不用純黑）。 */
export const INK = "#33261c";
/** 內部細節：較淡的棕，線也較細。 */
export const INK_SOFT = "#5c4838";

export type Rng = () => number;

/** 固定種子的亂數（Park–Miller）。 */
export function rng(seed: number): Rng {
  let s = seed % 2147483647;
  if (s <= 0) s += 2147483646;
  return () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
}

/** 字串 → 種子：同一條線每次畫出來的筆觸都一樣。 */
export function hashSeed(text: string): number {
  let hsh = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hsh ^= text.charCodeAt(i);
    hsh = Math.imul(hsh, 16777619);
  }
  return (hsh >>> 0) % 2147483646 + 1;
}

type Attrs = Record<string, string | number | undefined>;

function num(v: string | number): string {
  return typeof v === "number" ? String(Math.round(v * 10) / 10) : v;
}

/** 組一個 SVG 元素字串；children 為空時自我關閉。 */
export function h(tag: string, attrs: Attrs = {}, children = ""): string {
  let out = `<${tag}`;
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== undefined) out += ` ${k}="${num(v)}"`;
  }
  return children ? `${out}>${children}</${tag}>` : `${out}/>`;
}

export function g(attrs: Attrs, ...children: string[]): string {
  return h("g", attrs, children.join(""));
}

/** 鉛筆上色的色塊（帶紙紋與顏料深淺，不含墨線）。 */
export function fillPath(d: string, fill: string, extra: Attrs = {}): string {
  return h("path", { d, fill, filter: "url(#cs-pencil)", ...extra });
}

/**
 * 手繪墨線。粗的外輪廓畫兩層：底下一條細的連續線，上面一條粗的斷續線（斷點隨機、依路徑固定），
 * 疊起來就是有粗有細、偶爾提筆的線，而不是機器畫的等寬線。細線（內部細節）用較淡的棕色、單層。
 */
export function inkPath(d: string, width = 2.6, extra: Attrs = {}): string {
  const base = {
    d,
    fill: "none",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    filter: "url(#cs-rough)",
  };
  if (width < 1.9) return h("path", { ...base, stroke: INK_SOFT, "stroke-width": width, ...extra });
  const r = rng(hashSeed(d));
  const dash: number[] = [];
  for (let i = 0; i < 6; i += 1) dash.push(18 + Math.round(r() * 60), 3 + Math.round(r() * 9));
  return (
    h("path", { ...base, stroke: INK, "stroke-width": width * 0.6, ...extra }) +
    h("path", { ...base, stroke: INK, "stroke-width": width, "stroke-dasharray": dash.join(" "), "stroke-dashoffset": Math.round(r() * 80), ...extra })
  );
}

/** 一塊有色＋墨線外框；hatch 給就再疊一層斜線陰影。 */
export function shape(d: string, fill: string, width = 2.6, hatch?: "cs-hatch" | "cs-hatch-fine" | "cs-cross"): string {
  return (
    fillPath(d, fill) +
    (hatch ? h("path", { d, fill: `url(#${hatch})`, filter: "url(#cs-rough)" }) : "") +
    inkPath(d, width)
  );
}

/** 只在陰影那一側疊鉛筆斜線（d 是陰影區域）。 */
export function hatchArea(d: string, kind: "cs-hatch" | "cs-hatch-fine" | "cs-cross" = "cs-hatch-fine", opacity = 1): string {
  return h("path", { d, fill: `url(#${kind})`, filter: "url(#cs-rough)", opacity });
}

/** 地上的影子。 */
export function shadow(cx: number, cy: number, rx: number, ry: number, opacity = 0.2): string {
  return h("ellipse", { cx, cy, rx, ry, fill: INK, opacity, filter: "url(#cs-pencil)" });
}

/** 用 logo 圖（透明底黑線）當遮罩印出任意顏色的露坑 logo。 */
export function brandMark(id: string, href: string, x: number, y: number, w: number, hgt: number, color: string): string {
  return (
    h("mask", { id, maskUnits: "userSpaceOnUse", x, y, width: w, height: hgt, style: "mask-type:alpha" }, h("image", { href, x, y, width: w, height: hgt })) +
    h("rect", { x, y, width: w, height: hgt, fill: color, mask: `url(#${id})` })
  );
}

export const LOGO_MARK = "/brand/luken-mark.png";

function glyphRun(glyphs: Record<string, Glyph>, text: string, size: number, tracking: number): { d: string; width: number } {
  const k = size / 1000;
  let x = 0;
  let d = "";
  for (const ch of text) {
    const gl = glyphs[ch];
    // 字形是預先從字型取出的（scripts/camping/extract_glyphs.py），沒取到的字畫不出來，寧可直接報錯
    if (!gl) throw new Error(`doodleText: 字形表裡沒有「${ch}」，請加進 extract_glyphs.py 重新產生`);
    d += `<path transform="translate(${num(x)} 0) scale(${Number(k.toFixed(4))})" d="${gl.d}"/>`;
    x += gl.adv * k + tracking;
  }
  return { d, width: Math.max(0, x - tracking) };
}

export type DoodleStyle = {
  /** marker：馬克筆手寫（Long Cang）；round：泡泡圓體（粉圓）。 */
  font: "marker" | "round";
  size: number;
  fill: string;
  outline?: string;
  outlineWidth?: number;
  /** 錯位陰影距離（0 不畫）。 */
  drop?: number;
  texture?: "hatch" | "dots" | "none";
};

/**
 * 塗鴉字：粗外框＋錯位陰影＋紋理。原點在文字基線中央。
 * 店主 2026-09-27：四種塗鴉樣式都可以，顏色與字級視場合調整。
 */
export function doodleText(text: string, style: DoodleStyle): string {
  const glyphs = style.font === "marker" ? MARKER_GLYPHS : ROUND_GLYPHS;
  const tracking = style.size * (style.font === "marker" ? -0.04 : 0.02);
  const run = glyphRun(glyphs, text, style.size, tracking);
  const outline = style.outline ?? INK;
  const ow = style.outlineWidth ?? style.size * 0.12;
  const drop = style.drop ?? style.size * 0.06;
  const at = `translate(${num(-run.width / 2)} 0)`;
  const layer = (attrs: Attrs) => g({ transform: at, ...attrs }, run.d);
  let out = "";
  if (drop > 0) {
    out += g({ transform: `translate(${num(drop)} ${num(drop)})` }, layer({ fill: outline, stroke: outline, "stroke-width": ow, "stroke-linejoin": "round" }));
  }
  out += layer({ fill: outline, stroke: outline, "stroke-width": ow, "stroke-linejoin": "round" });
  out += layer({ fill: style.fill });
  const texture = style.texture ?? "hatch";
  if (texture !== "none") out += layer({ fill: texture === "dots" ? "url(#cs-dots)" : "url(#cs-hatch-fine)" });
  return g({ filter: "url(#cs-rough)" }, out);
}

/** 塗鴉小星星（原點在中心）。 */
export function doodleStar(size: number, fill = "#f0c43b"): string {
  const pts: string[] = [];
  for (let i = 0; i < 10; i += 1) {
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    const rr = i % 2 === 0 ? size : size * 0.45;
    pts.push(`${num(Math.cos(a) * rr)} ${num(Math.sin(a) * rr)}`);
  }
  return h("path", { d: `M${pts.join(" L")} Z`, fill, stroke: INK, "stroke-width": Math.max(1.6, size * 0.14), "stroke-linejoin": "round", filter: "url(#cs-rough)" });
}
