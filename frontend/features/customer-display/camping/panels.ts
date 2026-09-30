// 地面層的七格場景（每格 1000×1400，靜態不動的部分）。會動的角色另外疊在上面（figures.ts）。
//   0–2 開露營車上山　3 營地停車卸貨　4 主營地（帳篷＋木棧台營桌，夜晚入帳篷）　5 天幕營位（避雨、串燈、暖爐、營火）　6 雲海
import { cloud, flowers, grassField, pine, stone } from "./nature";
import { beanBag, campChair, campTable, deck, dripperSet, firePit, kettle } from "./props";
import {
  INK,
  doodleText,
  fillPath,
  g,
  h,
  hatchArea,
  inkPath,
  rng,
  shadow,
  shape,
  type Rng,
} from "./svg";

export const PANEL_W = 1000;
export const STAGE_H = 1400;
export const PANEL_COUNT = 7;

/** 道路中心線高度（世界座標 x）：開車那三格的起伏，露營車沿著它上下顛簸。 */
export function roadY(x: number): number {
  return 1262 + 22 * Math.sin(x / 330) + 8 * Math.sin(x / 97);
}

/** 草原：兩層底色（遠處偏黃綠、近處偏深）、乾筆刷的橫向筆觸、成叢的草（有密有疏有空白）。 */
function meadowBase(r: Rng, topY = 1040): string {
  let brush = "";
  for (let i = 0; i < 40; i += 1) {
    const x = r() * 1000;
    const y = topY + 20 + Math.pow(r(), 0.9) * (1380 - topY);
    const len = 20 + r() * 60;
    brush += h("path", { d: `M${x.toFixed(0)} ${y.toFixed(0)} q${(len / 2).toFixed(0)} ${(-2 + r() * 4).toFixed(1)} ${len.toFixed(0)} 0`, stroke: r() > 0.5 ? "#7f9a52" : "#b3c47c", "stroke-width": 2 + r() * 3, opacity: 0.35, fill: "none", "stroke-linecap": "round" });
  }
  return (
    // 大片草地只用紙紋顆粒（不做大範圍顏料深淺），格與格接起來才不會看到接縫
    g(
      { filter: "url(#cs-grain)" },
      h("path", { d: `M-20 ${topY} C250 ${topY - 20} 600 ${topY + 10} 1020 ${topY} L1020 1400 L-20 1400 Z`, fill: "#a3b86c" }),
      h("path", { d: `M-20 ${topY + 130} C300 ${topY + 110} 700 ${topY + 145} 1020 ${topY + 130} L1020 1400 L-20 1400 Z`, fill: "#8fa85c" }),
      h("path", { d: `M-20 ${topY + 260} C300 ${topY + 250} 700 ${topY + 270} 1020 ${topY + 260} L1020 1400 L-20 1400 Z`, fill: "#7f9a4f" }),
    ) +
    h("path", { d: `M-20 ${topY} C250 ${topY - 20} 600 ${topY + 10} 1020 ${topY}`, stroke: "#6f8a4a", "stroke-width": 1.4, fill: "none", opacity: 0.8, filter: "url(#cs-rough)" }) +
    brush +
    grassField(r, -10, 1010, topY + 4, 1300, 24)
  );
}

/** 最前景的草叢（比較高、顏色較深，蓋住下緣）。 */
function frontGrass(r: Rng, y0 = 1310): string {
  return grassField(r, -20, 1020, y0, 1420, 11, 1.5);
}

function bush(x: number, y: number, s: number): string {
  // 幾團葉叢疊起來：外輪廓鋸齒、背光面深色與斜線、幾片亮葉
  const r = rng(Math.round(x * 7 + y));
  let out = h("ellipse", { cx: x, cy: y + 1, rx: 46 * s, ry: 6 * s, fill: INK, opacity: 0.22 });
  const lumps: [number, number, number][] = [[-22, -18, 24], [4, -30, 28], [26, -16, 22], [-2, -10, 26]];
  for (const [dx, dy, rr] of lumps) {
    const cx = x + dx * s;
    const cy = y + dy * s;
    const pts: string[] = [];
    for (let k = 0; k < 14; k += 1) {
      const a2 = (k / 14) * Math.PI * 2;
      const rad = rr * s * (k % 2 ? 0.86 : 1) * (0.92 + r() * 0.12);
      pts.push(`${(cx + Math.cos(a2) * rad).toFixed(1)} ${(cy + Math.sin(a2) * rad * 0.8).toFixed(1)}`);
    }
    const d = `M${pts.join(" L")} Z`;
    out += fillPath(d, "#6c8c4a") + inkPath(d, 1.6);
    out += hatchArea(`M${cx} ${cy - rr * s * 0.2} L${cx + rr * s} ${cy} L${cx + rr * s * 0.6} ${cy + rr * s * 0.7} L${cx} ${cy + rr * s * 0.8} Z`, "cs-hatch-fine", 0.8);
    out += h("path", { d: `M${cx - rr * s * 0.5} ${cy - rr * s * 0.3} q4 -4 8 -2`, stroke: "#9cbb6e", "stroke-width": 2, fill: "none", "stroke-linecap": "round" });
  }
  return out;
}

/** 木頭指示牌。 */
function signpost(x: number, y: number, text: string, arrow: boolean, font: "marker" | "round" = "marker"): string {
  const w = 190;
  return g(
    { transform: `translate(${x} ${y})` },
    shadow(0, 0, 30, 5),
    shape("M-6 0 L6 0 L6 -120 L-6 -120 Z", "#8a5a34", 2.2),
    shape(arrow ? `M${-w / 2} -170 L${w / 2 - 10} -170 L${w / 2 + 20} -140 L${w / 2 - 10} -110 L${-w / 2} -110 Z` : `M${-w / 2} -170 L${w / 2} -170 L${w / 2} -110 L${-w / 2} -110 Z`, "#c99a63", 2.6),
    inkPath(`M${-w / 2 + 10} -150 q40 -3 80 0 M${-w / 2 + 30} -126 q50 3 90 0`, 1, { opacity: 0.4 }),
    g({ transform: "translate(-6 -122)" }, doodleText(text, { font, size: font === "marker" ? 48 : 40, fill: "#fff1d6", outline: "#3a2210", outlineWidth: 6, drop: 3, texture: "none" })),
  );
}

function fence(x0: number, x1: number, y: number): string {
  let posts = "";
  for (let x = x0; x <= x1; x += 70) posts += shape(`M${x - 5} ${y} L${x + 5} ${y} L${x + 5} ${y - 56} L${x} ${y - 64} L${x - 5} ${y - 56} Z`, "#b98352", 1.8);
  return shape(`M${x0} ${y - 44} L${x1} ${y - 44} L${x1} ${y - 36} L${x0} ${y - 36} Z M${x0} ${y - 22} L${x1} ${y - 22} L${x1} ${y - 14} L${x0} ${y - 14} Z`, "#c99a63", 1.8) + posts;
}

/** 開車那三格：道路、柵欄、松樹、小池塘、指示牌。 */
function drivePanel(index: number): string {
  const r = rng(101 + index * 17);
  const x0 = index * PANEL_W;
  const pts: [number, number][] = [];
  for (let x = -20; x <= 1020; x += 20) pts.push([x, roadY(x0 + x)]);
  const line = (dy: number, list: [number, number][]) => list.map(([x, y]) => `${x} ${(y + dy).toFixed(1)}`).join(" L");
  const roadD = `M${line(-40, pts)} L${line(40, [...pts].reverse())} Z`;
  const center = line(0, pts);
  const edgeTop = line(-40, pts);
  const edgeBottom = line(40, pts);
  let trees = "";
  for (let i = 0; i < 5; i += 1) {
    const x = 60 + i * 200 + r() * 90;
    trees += pine(x, 1120 + r() * 50, 0.9 + r() * 0.5);
  }
  let extras = "";
  if (index === 0) {
    extras += bush(120, 1190, 1) + bush(820, 1180, 0.8) + fence(360, 700, 1214);
  } else if (index === 1) {
    // 小池塘＋兩隻鴨子
    extras += shape("M380 1172 C420 1140 640 1140 690 1170 C700 1196 400 1200 380 1172 Z", "#7fb3c9", 2.4);
    extras += inkPath("M430 1168 q30 -4 60 0 M540 1176 q24 -3 48 0", 1.4, { opacity: 0.6, stroke: "#fff" });
    for (const [dx, s] of [[500, 1], [580, 0.8]] as const) {
      extras += g({ transform: `translate(${dx} 1162) scale(${s})` }, shape("M-14 0 C-16 -12 10 -14 14 -4 C18 -16 30 -14 28 -6 L34 -4 L28 0 C22 8 -10 8 -14 0 Z", "#f5f0e4", 1.8), h("circle", { cx: 24, cy: -9, r: 1.6, fill: INK }));
    }
    extras += bush(250, 1200, 0.9) + fence(740, 1000, 1214);
  } else {
    extras += fence(0, 280, 1214) + bush(420, 1196, 1.1);
    extras += signpost(780, 1212, "露坑", true);
  }
  return (
    meadowBase(r) +
    trees +
    extras +
    fillPath(roadD, "#b9a58a") +
    h("path", { d: roadD, fill: "url(#cs-hatch-fine)", filter: "url(#cs-rough)", opacity: 0.6 }) +
    inkPath(`M${edgeTop}`, 2.6) +
    inkPath(`M${edgeBottom}`, 2.6) +
    inkPath(`M${center}`, 3, { stroke: "#f5efe0", "stroke-dasharray": "28 26", filter: undefined }) +
    frontGrass(r) +
    flowers(r, 0, 1000, 1320, 1390, 14)
  );
}

/** 營地停車格：碎石地、營地牌子。 */
function campsitePanel(): string {
  const r = rng(303);
  let gravel = "";
  for (let i = 0; i < 90; i += 1) gravel += h("ellipse", { cx: 120 + r() * 640, cy: 1230 + r() * 60, rx: 2 + r() * 4, ry: 1.5 + r() * 2, fill: r() > 0.5 ? "#a09a8c" : "#8a8478" });
  return (
    meadowBase(r) +
    pine(60, 1130, 1.3) +
    pine(140, 1110, 0.9) +
    pine(930, 1120, 1.2) +
    fillPath("M100 1224 C300 1210 600 1212 790 1226 C800 1290 110 1298 100 1224 Z", "#cbbd9f") +
    gravel +
    inkPath("M100 1224 C300 1210 600 1212 790 1226", 1.6, { opacity: 0.6 }) +
    signpost(730, 1172, "露坑", false) +
    bush(40, 1260, 0.9) +
    frontGrass(r, 1320) +
    flowers(r, 0, 1000, 1330, 1390, 12)
  );
}

/** 帳篷＋泡咖啡那格（店主定稿的樣張，精緻版）：木棧台、營火堆、營桌與咖啡器材、露營椅。帳篷與人物是角色，另外疊上。 */
function coffeePanel(): string {
  const r = rng(11);
  return (
    meadowBase(r) +
    // 營火旁被踩出來的一小塊泥地
    fillPath("M190 1262 C220 1226 440 1222 480 1256 C470 1290 220 1296 190 1262 Z", "#b59d73", { opacity: 0.55 }) +
    pine(962, 1016, 1.3, false, "mid") +
    pine(90, 1020, 1.15, false, "near") +
    deck(r) +
    g({ transform: "translate(330 1250)" }, firePit(r)) +
    campTable(r) +
    kettle() +
    dripperSet() +
    beanBag() +
    campChair() +
    stone(r, 610, 1310, 14) +
    stone(r, 632, 1318, 9) +
    frontGrass(r) +
    flowers(r, 40, 300, 1330, 1380, 4) +
    flowers(r, 760, 980, 1335, 1385, 3)
  );
}

/** 懸崖看雲海（後景）：一整片雲海蓋住山腳，兩側往面板邊緣收低，才不會跟隔壁格硬接。 */
function cliffBack(): string {
  // 雲海：遠處一整片柔和的雲層（幾道淡淡的層次線、偶爾一團隆起），不是一朵朵重複的雲
  const r = rng(505);
  const top = "M-20 1045 C60 1040 140 950 260 930 C420 915 640 920 780 930 C900 948 950 1036 1020 1045";
  let bands = "";
  for (let i = 0; i < 6; i += 1) {
    const y = 960 + i * 70 + r() * 14;
    bands += `M${120 + r() * 60} ${y.toFixed(0)} C${300 + r() * 60} ${(y - 10).toFixed(0)} ${560 + r() * 60} ${(y + 8).toFixed(0)} ${880 + r() * 60} ${(y - 4).toFixed(0)} `;
  }
  let humps = "";
  for (let i = 0; i < 5; i += 1) {
    const x = 220 + i * 150 + r() * 60;
    const y = 950 + (i % 2) * 110 + r() * 40;
    humps += g({ transform: `translate(${x.toFixed(0)} ${y.toFixed(0)})`, opacity: (0.7 + (i % 3) * 0.12).toFixed(2) }, cloud(150 + r() * 110, i % 2 ? "#f7f5f0" : "#fbf8f1", "#e3e2e6", i));
  }
  return (
    fillPath(`${top} L1020 1420 L-20 1420 Z`, "#f6f3ec") +
    h("path", { d: `${top} L1020 1420 L-20 1420 Z`, fill: "url(#cs-haze)", opacity: 0.5 }) +
    h("path", { d: bands, stroke: "#c9cfd6", "stroke-width": 1.4, fill: "none", opacity: 0.8, "stroke-linecap": "round" }) +
    humps +
    h("path", { d: top, stroke: "#9aa3aa", "stroke-width": 1.2, fill: "none", opacity: 0.6, filter: "url(#cs-rough)" })
  );
}

/** 連續的前景草地；雲海在護欄後方，散步與回程都走同一條陸地。 */
export function cliffFront(): string {
  return g({ class: "cs-lookout-ground" },
    meadowBase(rng(508), 1040),
    fillPath("M-20 1152 Q360 1120 1020 1160 L1020 1200 Q380 1170 -20 1204 Z", "#b5ac89", { opacity: 0.5 }),
    fence(160, 900, 1080),
    pine(40, 1080, 1.1),
    signpost(110, 1100, "露坑", false),
    frontGrass(rng(510)),
  );
}

/**
 * 天幕營位（天幕、暖爐、火焰是角色，另外疊上）：草地、兩側松樹、天幕下的保冷箱與木箱、
 * 前方的營火堆（和泡咖啡那一幕同一種畫法）、坐的木頭。
 */
function tarpSitePanel(): string {
  const r = rng(606);
  return (
    meadowBase(r) +
    pine(70, 1040, 1.25, false, "near") +
    pine(955, 1030, 1.1, false, "mid") +
    // 木箱（層板＋提手孔）與保冷箱
    shadow(705, 1172, 60, 6, 0.24) +
    shape("M650 1172 L760 1172 L760 1112 L650 1112 Z", "#c99a63", 2.2) +
    inkPath("M650 1132 L760 1132 M650 1152 L760 1152 M672 1122 l12 0 M726 1122 l12 0", 1.1) +
    hatchArea("M736 1112 L760 1112 L760 1172 L736 1172 Z", "cs-hatch-fine", 0.9) +
    shadow(820, 1180, 58, 6, 0.24) +
    shape("M772 1180 L868 1180 L864 1124 L776 1124 Z", "#5fa38a", 2.2, "cs-hatch-fine") +
    shape("M768 1112 L872 1112 L872 1126 L768 1126 Z", "#f3e9d2", 1.8) +
    inkPath("M806 1104 L834 1104 M806 1104 L802 1112 M834 1104 L838 1112", 1.6) +
    // 營火堆（火焰是角色）；坐的木頭畫在烤棉花糖的人身上
    g({ transform: "translate(620 1290) scale(0.9)" }, firePit(r)) +
    frontGrass(r) +
    flowers(r, 0, 1000, 1330, 1390, 6)
  );
}

/** 第 index 格的靜態內容（區域座標 0–1000）。 */
export function panelArt(index: number): string {
  if (index <= 2) return drivePanel(index);
  if (index === 3) return campsitePanel();
  if (index === 4) return coffeePanel();
  if (index === 5) return tarpSitePanel();
  return cliffBack();
}
