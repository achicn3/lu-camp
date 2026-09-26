// 手繪風 SVG 的小工具：全部回傳字串，由 CampingScene 一次塞進 DOM。
// 畫風（店主 2026-09-27 定稿的樣張）：紙紋底、鉛筆上色（pencil 濾鏡）、抖動墨線（rough 濾鏡）、
// 斜線陰影。亂數一律用固定種子，每次開機畫出來都一樣。
import { MARKER_GLYPHS, ROUND_GLYPHS, type Glyph } from "./glyphs";

export const INK = "#2a2018";

export type Rng = () => number;

/** 固定種子的亂數（Park–Miller）。 */
export function rng(seed: number): Rng {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
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

/** 鉛筆上色的色塊（不含墨線）。 */
export function fillPath(d: string, fill: string, extra: Attrs = {}): string {
  return h("path", { d, fill, filter: "url(#cs-pencil)", ...extra });
}

/** 抖動墨線。 */
export function inkPath(d: string, width = 2.6, extra: Attrs = {}): string {
  return h("path", {
    d,
    fill: "none",
    stroke: INK,
    "stroke-width": width,
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    filter: "url(#cs-rough)",
    ...extra,
  });
}

/** 一塊有色＋墨線外框；hatch 給就再疊一層斜線陰影。 */
export function shape(d: string, fill: string, width = 2.6, hatch?: "cs-hatch" | "cs-hatch-fine" | "cs-cross"): string {
  return (
    fillPath(d, fill) +
    (hatch ? h("path", { d, fill: `url(#${hatch})`, filter: "url(#cs-rough)" }) : "") +
    inkPath(d, width)
  );
}

/** 地上的影子。 */
export function shadow(cx: number, cy: number, rx: number, ry: number, opacity = 0.2): string {
  return h("ellipse", { cx, cy, rx, ry, fill: INK, opacity, filter: "url(#cs-pencil)" });
}

/** 一棵層次松樹（原點在樹幹底）。 */
export function pine(x: number, y: number, s: number, dark = false): string {
  const light = dark ? "#40593a" : "#58774a";
  const shade = dark ? "#2f4429" : "#46613b";
  let body = h("rect", { x: -3, y: -14, width: 6, height: 14, fill: "#4a3322" });
  for (const [ty, w] of [[-116, 16], [-96, 22], [-74, 28], [-50, 34]] as const) {
    body += h("path", {
      d: `M0 ${ty - 10} L${-w} ${ty + 18} L${w} ${ty + 18} Z`,
      fill: light,
      stroke: INK,
      "stroke-width": 1.8,
      "stroke-linejoin": "round",
      filter: "url(#cs-rough)",
    });
    body += h("path", { d: `M0 ${ty - 10} L${w} ${ty + 18} L${w * 0.2} ${ty + 18} Z`, fill: shade });
  }
  return g({ transform: `translate(${num(x)} ${num(y)}) scale(${num(s)})` }, body);
}

/** 一片草葉。 */
export function blade(r: Rng, x: number, baseY: number, height: number, color: string, width: number): string {
  const lean = (r() - 0.5) * height * 0.6;
  return h("path", {
    d: `M${num(x)} ${num(baseY)} Q${num(x + lean * 0.4)} ${num(baseY - height * 0.6)} ${num(x + lean)} ${num(baseY - height)}`,
    stroke: color,
    "stroke-width": width,
  });
}

/** 一片草地：在 [x0,x1]×[y0,y1] 撒 count 片草，越往下越高越粗（近大遠小）。 */
export function meadow(r: Rng, x0: number, x1: number, y0: number, y1: number, count: number, colors = ["#6f8c45", "#5c7a39"]): string {
  let out = "";
  for (let i = 0; i < count; i += 1) {
    const x = x0 + r() * (x1 - x0);
    const y = y0 + Math.pow(r(), 1.4) * (y1 - y0);
    const depth = (y - y0) / Math.max(1, y1 - y0);
    out += blade(r, x, y, 6 + depth * 16 + r() * 6, colors[r() > 0.5 ? 0 : 1] ?? "#6f8c45", 1 + depth * 1.2);
  }
  return g({ "stroke-linecap": "round", fill: "none" }, out);
}

/** 小花（白／黃五瓣）。 */
export function flowers(r: Rng, x0: number, x1: number, y0: number, y1: number, count: number): string {
  let out = "";
  for (let i = 0; i < count; i += 1) {
    const x = x0 + r() * (x1 - x0);
    const y = y0 + r() * (y1 - y0);
    const c = r() > 0.5 ? "#f5f0e4" : "#f2c94c";
    const rad = 3 + r() * 2;
    for (let k = 0; k < 5; k += 1) {
      const a = (k / 5) * Math.PI * 2;
      out += h("circle", { cx: x + Math.cos(a) * rad, cy: y + Math.sin(a) * rad, r: rad * 0.7, fill: c, stroke: INK, "stroke-width": 0.8 });
    }
    out += h("circle", { cx: x, cy: y, r: rad * 0.55, fill: "#e0892c" });
  }
  return out;
}

/** 石頭（含一筆亮面）。 */
export function stone(r: Rng, x: number, y: number, w: number): string {
  return (
    h("ellipse", { cx: x, cy: y, rx: w, ry: w * 0.62, fill: r() > 0.5 ? "#9a9a92" : "#86867e", stroke: INK, "stroke-width": 2, filter: "url(#cs-rough)" }) +
    h("path", { d: `M${num(x - w * 0.5)} ${num(y - w * 0.2)} q${num(w * 0.3)} ${num(-w * 0.35)} ${num(w * 0.6)} ${num(-w * 0.2)}`, stroke: "#fff", "stroke-width": 1.6, fill: "none", opacity: 0.7 })
  );
}

/** 塗鴉太陽：色塊＋短筆觸＋放射線（原點在圓心）。 */
export function scribbleSun(r: Rng, radius = 84): string {
  let strokes = "";
  for (let i = 0; i < 260; i += 1) {
    const a = r() * Math.PI * 2;
    const rr = Math.sqrt(r()) * (radius - 8);
    const x = Math.cos(a) * rr;
    const y = Math.sin(a) * rr;
    const ang = -0.6 + r() * 0.4;
    const len = 9 + r() * 13;
    strokes += h("line", { x1: x, y1: y, x2: x + Math.cos(ang) * len, y2: y + Math.sin(ang) * len, stroke: r() > 0.5 ? "#e5892c" : "#d8741f" });
  }
  let rays = "";
  for (let i = 0; i < 14; i += 1) {
    const a = (i / 14) * Math.PI * 2 + 0.1;
    const r1 = radius + 18 + r() * 6;
    const r2 = r1 + 22 + r() * 14;
    rays += h("line", { x1: Math.cos(a) * r1, y1: Math.sin(a) * r1, x2: Math.cos(a) * r2, y2: Math.sin(a) * r2 });
  }
  return (
    h("circle", { r: radius, fill: "#f3c16a", filter: "url(#cs-pencil)" }) +
    g({ "stroke-width": 1.8, "stroke-linecap": "round", opacity: 0.8 }, strokes) +
    h("circle", { r: radius, fill: "none", stroke: INK, "stroke-width": 2.8, filter: "url(#cs-rough)" }) +
    g({ class: "cs-sun-rays", stroke: "#d8741f", "stroke-width": 2.6, "stroke-linecap": "round", filter: "url(#cs-rough)" }, rays)
  );
}

/** 一朵手繪雲（原點在雲底中央），w 為寬。 */
export function cloud(w: number, fill = "#fbf7ee", shade = "#dfe3e6"): string {
  const s = w / 200;
  const d = `M${-100 * s} 0 C${-112 * s} ${-26 * s} ${-84 * s} ${-44 * s} ${-60 * s} ${-36 * s} C${-56 * s} ${-70 * s} ${-12 * s} ${-80 * s} ${6 * s} ${-56 * s} C${22 * s} ${-84 * s} ${72 * s} ${-74 * s} ${70 * s} ${-40 * s} C${96 * s} ${-44 * s} ${114 * s} ${-18 * s} ${100 * s} 0 Z`;
  return (
    fillPath(d, fill) +
    h("path", { d: `M${-96 * s} 0 C${-60 * s} ${-14 * s} ${40 * s} ${-16 * s} ${98 * s} ${-4 * s} L${100 * s} 0 Z`, fill: shade, opacity: 0.8 }) +
    inkPath(d, 2.2)
  );
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
