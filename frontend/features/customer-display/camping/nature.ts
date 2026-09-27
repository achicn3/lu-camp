// 自然景物：松樹（多種變化＋遠近）、成叢的草、花、石頭、雲、太陽。
// 店主 2026-09-27：不要一整排一模一樣的樹、不要平均撒的草；遠景低對比少細節，前景深色多細節。
import { INK, INK_SOFT, type Rng, fillPath, g, h, hashSeed, hatchArea, inkPath, rng } from "./svg";

export type Depth = "near" | "mid" | "far";

const PINE_PALETTE: Record<Depth, { base: string; shade: string; light: string; ink: string; trunk: string }> = {
  near: { base: "#4c6b3f", shade: "#36502e", light: "#6e8c56", ink: INK, trunk: "#5a3d28" },
  mid: { base: "#62805a", shade: "#4d6948", light: "#7e9a6c", ink: "#4d5c47", trunk: "#6a5040" },
  far: { base: "#8d9f95", shade: "#7c8e86", light: "#a0b0a6", ink: "#7a8880", trunk: "#7c8e86" },
};

function f(v: number): string {
  return (Math.round(v * 10) / 10).toString();
}

/**
 * 一棵松樹，原點在樹幹底。每棵依種子決定高矮胖瘦、層數、歪斜、枝葉疏密，
 * 枝尖是鋸齒不是直線三角形；近景有針葉筆觸與樹皮，遠景只剩色塊與淡線。
 */
export function pineTree(r: Rng, depth: Depth): string {
  const pal = PINE_PALETTE[depth];
  const height = 130 * (0.75 + r() * 0.6);
  const girth = 0.26 + r() * 0.2;
  const tiers = 4 + Math.floor(r() * 3);
  const sparse = r() > 0.6;
  const trunkH = height * (0.1 + r() * 0.06);
  let out = "";
  if (depth !== "far") {
    out += fillPath(`M-4 0 L4 0 L3 ${-trunkH - 6} L-3 ${-trunkH - 6} Z`, pal.trunk);
    if (depth === "near") out += inkPath(`M-4 0 L-3 ${-trunkH} M4 0 L3 ${-trunkH} M-1 -3 L0 ${-trunkH * 0.6}`, 1.1);
  }
  const top = -height;
  const span = height - trunkH;
  for (let i = 0; i < tiers; i += 1) {
    const t = (i + 1) / tiers;
    const yTop = top + (i / tiers) * span * 0.78;
    const yBot = top + t * span * (sparse ? 0.92 : 1) + (i === tiers - 1 ? 0 : span * 0.06);
    const w = height * girth * (0.28 + 0.72 * t) * (0.9 + r() * 0.2);
    const spikes = 3 + Math.floor(r() * 3);
    const bottom: string[] = [];
    for (let k = 0; k <= spikes * 2; k += 1) {
      const x = -w + (2 * w * k) / (spikes * 2);
      const y = k % 2 === 0 ? yBot + r() * 3 : yBot - (yBot - yTop) * (0.12 + r() * 0.1);
      bottom.push(`${f(x + (r() - 0.5) * 3)} ${f(y)}`);
    }
    const midY = yTop + (yBot - yTop) * 0.55;
    const d = `M0 ${f(yTop)} L${f(-w * 0.5)} ${f(midY)} L${f(-w * 0.36)} ${f(midY + 3)} L${bottom.join(" L")} L${f(w * 0.36)} ${f(midY + 3)} L${f(w * 0.5)} ${f(midY)} Z`;
    out += fillPath(d, pal.base);
    out += h("path", { d: `M0 ${f(yTop)} L${f(w * 0.5)} ${f(midY)} L${f(w * 0.36)} ${f(midY + 3)} L${f(w)} ${f(yBot)} L${f(w * 0.08)} ${f(yBot - 2)} Z`, fill: pal.shade });
    if (depth === "near") {
      out += hatchArea(`M0 ${f(yTop)} L${f(w)} ${f(yBot)} L${f(w * 0.2)} ${f(yBot)} Z`, "cs-hatch-fine", 0.8);
      let needles = "";
      for (let k = 0; k < 7; k += 1) {
        const x = -w * 0.85 + r() * w * 1.7;
        needles += `M${f(x)} ${f(yBot - 3 - r() * 8)} l${f((r() - 0.5) * 6)} ${f(5 + r() * 5)} `;
      }
      out += inkPath(needles, 0.9);
      out += h("path", { d: `M${f(-w * 0.5)} ${f(midY - 2)} q${f(w * 0.2)} -4 ${f(w * 0.3)} -10`, stroke: pal.light, "stroke-width": 2, fill: "none", "stroke-linecap": "round" });
    } else if (depth === "mid" && r() > 0.4) {
      out += h("path", { d: `M${f(-w * 0.6)} ${f(yBot - 4)} l3 5 M${f(w * 0.3)} ${f(yBot - 5)} l-2 5`, stroke: INK_SOFT, "stroke-width": 0.8, fill: "none", opacity: 0.6 });
    }
    out += h("path", {
      d,
      fill: "none",
      stroke: pal.ink,
      "stroke-width": depth === "near" ? 1.7 : depth === "mid" ? 1.1 : 0.8,
      "stroke-linejoin": "round",
      filter: "url(#cs-rough)",
      opacity: depth === "far" ? 0.7 : 1,
    });
  }
  const lean = (r() - 0.5) * 8;
  return g({ transform: `skewX(${f(lean)})` }, out);
}

/** 相容舊呼叫：在 (x,y) 放一棵樹，種類由位置決定（同一位置永遠同一棵）。 */
export function pine(x: number, y: number, s: number, dark = false, depth: Depth = dark ? "near" : "mid"): string {
  const r = rng(hashSeed(`${f(x)},${f(y)}`));
  return g({ transform: `translate(${f(x)} ${f(y)}) scale(${f(s)})` }, pineTree(r, depth));
}

const GRASS = ["#5d7d39", "#6c8c43", "#7b9a4e", "#4f6d32", "#86a35a"];

/** 一片細長、根部較寬的草葉（填色而不是等寬線）。 */
function grassBlade(r: Rng, x: number, y: number, height: number, width: number): string {
  const lean = (r() - 0.5) * height * 0.9;
  const color = r() > 0.95 ? "#a9a35a" : (GRASS[Math.floor(r() * GRASS.length)] ?? "#6c8c43");
  const tipX = x + lean;
  const tipY = y - height;
  return h("path", {
    d: `M${f(x - width / 2)} ${f(y)} Q${f(x + lean * 0.3 - width * 0.2)} ${f(y - height * 0.55)} ${f(tipX)} ${f(tipY)} Q${f(x + lean * 0.35 + width * 0.3)} ${f(y - height * 0.5)} ${f(x + width / 2)} ${f(y)} Z`,
    fill: color,
  });
}

/**
 * 成叢的草：先挑幾個叢心（有密有疏、叢與叢之間留空），每叢十幾片長短不一的草葉；
 * 越靠近畫面下緣越高越寬。叢之間零星散幾片單葉當紋理。
 */
export function grassField(r: Rng, x0: number, x1: number, y0: number, y1: number, clusters: number, scale = 1): string {
  let out = "";
  const span = Math.max(1, y1 - y0);
  for (let c = 0; c < clusters; c += 1) {
    const cx = x0 + r() * (x1 - x0);
    const cy = y0 + Math.pow(r(), 0.8) * span;
    const depth = (cy - y0) / span;
    const blades = 5 + Math.floor(r() * (r() > 0.7 ? 18 : 9));
    const spread = (6 + depth * 18) * scale;
    for (let i = 0; i < blades; i += 1) {
      const bx = cx + (r() + r() - 1) * spread;
      const hgt = (7 + depth * 26) * scale * (0.55 + r() * 0.8);
      out += grassBlade(r, bx, cy + (r() - 0.5) * 3, hgt, (1.4 + depth * 2.4) * scale);
    }
  }
  for (let i = 0; i < clusters * 2; i += 1) {
    const x = x0 + r() * (x1 - x0);
    const y = y0 + r() * span;
    const depth = (y - y0) / span;
    out += grassBlade(r, x, y, (5 + depth * 12) * scale, (1 + depth * 1.4) * scale);
  }
  return g({ class: "cs-grass" }, out);
}

/** 相容舊呼叫：count 片草 → 對應的叢數。 */
export function meadow(r: Rng, x0: number, x1: number, y0: number, y1: number, count: number): string {
  return grassField(r, x0, x1, y0, y1, Math.max(4, Math.round(count / 11)));
}

/** 小花：少量點綴，三五朵一群。 */
export function flowers(r: Rng, x0: number, x1: number, y0: number, y1: number, count: number): string {
  let out = "";
  const groups = Math.max(1, Math.round(count / 4));
  for (let gi = 0; gi < groups; gi += 1) {
    const gx = x0 + r() * (x1 - x0);
    const gy = y0 + r() * (y1 - y0);
    const c = r() > 0.5 ? "#f6f1e4" : "#f0c64e";
    for (let i = 0; i < 2 + Math.floor(r() * 3); i += 1) {
      const x = gx + (r() - 0.5) * 30;
      const y = gy + (r() - 0.5) * 12;
      const rad = 2.4 + r() * 1.6;
      out += h("path", { d: `M${f(x)} ${f(y + rad)} q${f(-2)} 8 ${f(1)} 14`, stroke: "#557434", "stroke-width": 1.1, fill: "none" });
      for (let k = 0; k < 5; k += 1) {
        const a = (k / 5) * Math.PI * 2 + r();
        out += h("ellipse", { cx: x + Math.cos(a) * rad, cy: y + Math.sin(a) * rad, rx: rad * 0.75, ry: rad * 0.5, transform: `rotate(${f((a * 180) / Math.PI)} ${f(x + Math.cos(a) * rad)} ${f(y + Math.sin(a) * rad)})`, fill: c, stroke: INK_SOFT, "stroke-width": 0.6 });
      }
      out += h("circle", { cx: x, cy: y, r: rad * 0.5, fill: "#d9862c" });
    }
  }
  return out;
}

/** 石頭：色塊、背光面斜線、一筆亮面、底下的小影子。 */
export function stone(r: Rng, x: number, y: number, w: number): string {
  const hgt = w * (0.55 + r() * 0.15);
  const d = `M${f(x - w)} ${f(y + hgt * 0.3)} C${f(x - w * 1.05)} ${f(y - hgt * 0.6)} ${f(x - w * 0.2)} ${f(y - hgt * 1.05)} ${f(x + w * 0.35)} ${f(y - hgt * 0.8)} C${f(x + w * 1.05)} ${f(y - hgt * 0.5)} ${f(x + w * 1.05)} ${f(y + hgt * 0.35)} ${f(x + w * 0.6)} ${f(y + hgt * 0.55)} L${f(x - w * 0.7)} ${f(y + hgt * 0.6)} Z`;
  const tone = r() > 0.5 ? "#9b9a90" : "#8a887f";
  return (
    fillPath(d, tone) +
    hatchArea(`M${f(x + w * 0.1)} ${f(y - hgt * 0.7)} C${f(x + w)} ${f(y - hgt * 0.4)} ${f(x + w)} ${f(y + hgt * 0.4)} ${f(x + w * 0.5)} ${f(y + hgt * 0.55)} L${f(x)} ${f(y + hgt * 0.55)} Z`, "cs-hatch-fine", 0.9) +
    h("path", { d: `M${f(x - w * 0.6)} ${f(y - hgt * 0.2)} q${f(w * 0.25)} ${f(-hgt * 0.45)} ${f(w * 0.6)} ${f(-hgt * 0.5)}`, stroke: "#f4f1ea", "stroke-width": 1.6, fill: "none", opacity: 0.75, "stroke-linecap": "round" }) +
    inkPath(d, 1.9)
  );
}

/** 一朵手繪雲（原點在雲底中央），w 為寬：兩層雲團、底部淡灰藍陰影與細斜線、柔和的線。 */
export function cloud(w: number, fill = "#fbf8f0", shade = "#dde3e8"): string {
  const s = w / 200;
  const p = (x: number, y: number) => `${f(x * s)} ${f(y * s)}`;
  const d = `M${p(-100, 0)} C${p(-114, -24)} ${p(-88, -44)} ${p(-62, -36)} C${p(-60, -68)} ${p(-16, -80)} ${p(4, -56)} C${p(20, -86)} ${p(74, -76)} ${p(70, -40)} C${p(98, -46)} ${p(116, -16)} ${p(100, 0)} Z`;
  const inner = `M${p(-40, -30)} C${p(-30, -50)} ${p(-6, -52)} ${p(4, -38)} M${p(28, -44)} C${p(40, -58)} ${p(60, -54)} ${p(62, -36)}`;
  return (
    fillPath(d, fill) +
    h("path", { d: `M${p(-98, -2)} C${p(-60, -16)} ${p(40, -18)} ${p(99, -5)} L${p(100, 0)} L${p(-100, 0)} Z`, fill: shade, opacity: 0.85 }) +
    hatchArea(`M${p(-90, -2)} C${p(-50, -12)} ${p(40, -14)} ${p(92, -4)} L${p(92, 0)} L${p(-90, 0)} Z`, "cs-hatch-fine", 0.5) +
    h("path", { d: inner, fill: "none", stroke: "#8d98a3", "stroke-width": 1, opacity: 0.7, filter: "url(#cs-rough)" }) +
    h("path", { d, fill: "none", stroke: "#6c6a68", "stroke-width": 1.5, "stroke-linejoin": "round", filter: "url(#cs-rough)" })
  );
}

/** 太陽：暖色圓盤、少量彩色鉛筆筆觸、淡淡幾道短光線（不要蓋滿天空）。 */
export function scribbleSun(r: Rng, radius = 70): string {
  let strokes = "";
  for (let i = 0; i < 110; i += 1) {
    const a = r() * Math.PI * 2;
    const rr = Math.sqrt(r()) * (radius - 10);
    const x = Math.cos(a) * rr;
    const y = Math.sin(a) * rr;
    const ang = -0.6 + r() * 0.4;
    const len = 8 + r() * 10;
    strokes += h("line", { x1: x, y1: y, x2: x + Math.cos(ang) * len, y2: y + Math.sin(ang) * len, stroke: r() > 0.5 ? "#eba04a" : "#e08a36" });
  }
  let rays = "";
  for (let i = 0; i < 10; i += 1) {
    const a = (i / 10) * Math.PI * 2 + 0.2;
    const r1 = radius + 16 + r() * 6;
    const r2 = r1 + 12 + r() * 10;
    rays += h("line", { x1: Math.cos(a) * r1, y1: Math.sin(a) * r1, x2: Math.cos(a) * r2, y2: Math.sin(a) * r2 });
  }
  return (
    h("circle", { r: radius + 34, fill: "url(#cs-glow)", opacity: 0.35 }) +
    h("circle", { r: radius, fill: "#f4c877", filter: "url(#cs-pencil)" }) +
    g({ "stroke-width": 1.4, "stroke-linecap": "round", opacity: 0.55 }, strokes) +
    h("circle", { r: radius, fill: "none", stroke: "#b8702e", "stroke-width": 1.8, filter: "url(#cs-rough)" }) +
    g({ class: "cs-sun-rays", stroke: "#dc9444", "stroke-width": 1.8, "stroke-linecap": "round", opacity: 0.8, filter: "url(#cs-rough)" }, rays)
  );
}
