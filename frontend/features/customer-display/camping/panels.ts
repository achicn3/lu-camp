// 地面層的八格場景（每格 1000×1400，靜態不動的部分）。會動的角色另外疊在上面（figures.ts）。
//   0–2 開露營車上山　3 營地停車卸貨　4 搭帳篷＋泡咖啡　5 懸崖看雲海　6 天幕下烤棉花糖　7 星空吊床
import { cloud, flowers, grassField, meadow, pine, stone } from "./nature";
import { beanBag, campChair, campTable, deck, dripperSet, firePit, kettle, lantern } from "./props";
import {
  INK,
  doodleText,
  fillPath,
  g,
  h,
  inkPath,
  rng,
  shadow,
  shape,
  type Rng,
} from "./svg";

export const PANEL_W = 1000;
export const STAGE_H = 1400;
export const PANEL_COUNT = 8;

/** 道路中心線高度（世界座標 x）：開車那三格的起伏，露營車沿著它上下顛簸。 */
export function roadY(x: number): number {
  return 1262 + 22 * Math.sin(x / 330) + 8 * Math.sin(x / 97);
}

/** 草原：兩層底色（遠處偏黃綠、近處偏深）、乾筆刷的橫向筆觸、成叢的草（有密有疏有空白）。 */
function meadowBase(r: Rng, topY = 1040): string {
  let brush = "";
  for (let i = 0; i < 70; i += 1) {
    const x = r() * 1000;
    const y = topY + 20 + Math.pow(r(), 0.9) * (1380 - topY);
    const len = 20 + r() * 60;
    brush += h("path", { d: `M${x.toFixed(0)} ${y.toFixed(0)} q${(len / 2).toFixed(0)} ${(-2 + r() * 4).toFixed(1)} ${len.toFixed(0)} 0`, stroke: r() > 0.5 ? "#7f9a52" : "#b3c47c", "stroke-width": 2 + r() * 3, opacity: 0.35, fill: "none", "stroke-linecap": "round" });
  }
  return (
    g(
      { filter: "url(#cs-pencil)" },
      h("path", { d: `M-20 ${topY} C250 ${topY - 20} 600 ${topY + 10} 1020 ${topY - 10} L1020 1400 L-20 1400 Z`, fill: "#a3b86c" }),
      h("path", { d: `M-20 ${topY + 130} C300 ${topY + 110} 700 ${topY + 145} 1020 ${topY + 120} L1020 1400 L-20 1400 Z`, fill: "#8fa85c" }),
      h("path", { d: `M-20 ${topY + 260} C300 ${topY + 250} 700 ${topY + 270} 1020 ${topY + 255} L1020 1400 L-20 1400 Z`, fill: "#7f9a4f" }),
    ) +
    h("path", { d: `M-20 ${topY} C250 ${topY - 20} 600 ${topY + 10} 1020 ${topY - 10}`, stroke: "#6f8a4a", "stroke-width": 1.4, fill: "none", opacity: 0.8, filter: "url(#cs-rough)" }) +
    brush +
    grassField(r, -10, 1010, topY + 4, 1300, 46)
  );
}

/** 最前景的草叢（比較高、顏色較深，蓋住下緣）。 */
function frontGrass(r: Rng, y0 = 1310): string {
  return grassField(r, -20, 1020, y0, 1420, 22, 1.5);
}

function bush(x: number, y: number, s: number): string {
  const d = `M${x - 40 * s} ${y} C${x - 50 * s} ${y - 30 * s} ${x - 20 * s} ${y - 50 * s} ${x} ${y - 38 * s} C${x + 20 * s} ${y - 56 * s} ${x + 52 * s} ${y - 30 * s} ${x + 42 * s} ${y} Z`;
  return shape(d, "#6f8f4c", 2.2, "cs-hatch-fine");
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
    signpost(700, 1172, "露坑", false) +
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
    pine(960, 1016, 1.35, false, "near") +
    pine(40, 1020, 1.15, false, "near") +
    deck(r) +
    g({ transform: "translate(330 1250)" }, firePit(r)) +
    campTable(r) +
    lantern(700, 1004) +
    kettle() +
    dripperSet() +
    beanBag() +
    campChair() +
    stone(r, 610, 1310, 14) +
    stone(r, 632, 1318, 9) +
    frontGrass(r) +
    flowers(r, 40, 360, 1320, 1380, 8) +
    flowers(r, 700, 980, 1330, 1385, 5)
  );
}

/** 懸崖看雲海（後景）：一整片雲海蓋住山腳，兩側往面板邊緣收低，才不會跟隔壁格硬接。 */
function cliffBack(): string {
  const r = rng(505);
  const top = "M-20 1045 C60 1040 140 950 260 930 C420 915 640 920 780 930 C900 948 950 1036 1020 1045";
  let puffs = "";
  for (let row = 0; row < 5; row += 1) {
    for (let i = 0; i < 7; i += 1) {
      const x = 180 + i * 105 + r() * 40 - (row % 2) * 50;
      const y = 975 + row * 95 + r() * 25;
      puffs += g({ transform: `translate(${x.toFixed(0)} ${y.toFixed(0)})` }, cloud(150 + r() * 70, "#fbf7ee", row % 2 ? "#e3e0e8" : "#e7e3dc"));
    }
  }
  return fillPath(`${top} L1020 1420 L-20 1420 Z`, "#f4f1ea") + inkPath(top, 1.6, { opacity: 0.5 }) + puffs;
}

/** 懸崖看雲海（前景）：左右兩片草地，邊緣是岩石；中間的缺口看下去是雲海。流動的雲夾在前後景之間。 */
export function cliffFront(): string {
  const r = rng(506);
  const left = "M-20 1030 C120 1024 300 1036 470 1046 C500 1120 540 1240 600 1420 L-20 1420 Z";
  const right = "M1020 1036 C960 1036 920 1042 890 1052 C876 1140 862 1260 850 1420 L1020 1420 Z";
  const rim = (d: string) => fillPath(d, "#9a9383") + h("path", { d, fill: "url(#cs-hatch)", filter: "url(#cs-rough)" });
  let rocks = "";
  for (let i = 0; i < 8; i += 1) {
    const t = i / 7;
    rocks += stone(r, 470 + t * 120 + r() * 10, 1060 + t * 340, 12 + r() * 10);
  }
  for (let i = 0; i < 5; i += 1) {
    const t = i / 4;
    rocks += stone(r, 890 - t * 36 + r() * 8, 1070 + t * 330, 10 + r() * 8);
  }
  return (
    fillPath(left, "#9bb468") +
    fillPath(right, "#9bb468") +
    rim("M470 1046 C500 1120 540 1240 600 1420 L560 1420 C510 1260 470 1140 440 1050 Z") +
    rim("M890 1052 C876 1140 862 1260 850 1420 L880 1420 C890 1260 902 1140 916 1050 Z") +
    meadow(r, -10, 450, 1036, 1400, 300) +
    meadow(r, 900, 1010, 1040, 1400, 70) +
    rocks +
    inkPath(left, 2.8) +
    inkPath(right, 2.8) +
    shape("M330 1032 L338 1032 L338 972 L330 972 Z M440 1042 L448 1042 L448 982 L440 982 Z", "#8a5a34", 2) +
    shape("M326 982 L452 992 L452 1000 L326 990 Z M326 1004 L452 1014 L452 1020 L326 1012 Z", "#b98352", 1.8) +
    pine(40, 1040, 1.1) +
    signpost(130, 1042, "雲海", false, "round")
  );
}

/** 天幕下烤棉花糖：天幕、營柱、串燈（燈泡的光在燈光層）、木頭座位旁的營火、保冷箱。 */
function eveningPanel(): string {
  const r = rng(606);
  let stones = "";
  for (let i = 0; i < 9; i += 1) {
    const a = Math.PI * (0.05 + (i / 8) * 0.9);
    stones += stone(r, Math.cos(a) * -70, Math.sin(a) * 16 + 6, 14 + r() * 8);
  }
  let bulbs = "";
  for (let i = 0; i < 9; i += 1) {
    const t = i / 8;
    const x = 170 + t * 640;
    const y = 842 + Math.sin(t * Math.PI) * 44;
    bulbs += h("circle", { cx: x, cy: y + 12, r: 7, fill: "#f7e3a0", stroke: INK, "stroke-width": 1.6 });
  }
  return (
    meadowBase(r) +
    pine(60, 1030, 1.2) +
    pine(950, 1020, 1.1) +
    // 天幕
    shape("M170 842 L500 770 L830 842 L760 900 L240 900 Z", "#e8674a", 2.8, "cs-hatch-fine") +
    fillPath("M240 900 L760 900 L830 842 L760 860 Z", "#c24f36") +
    inkPath("M500 770 L500 1150 M170 842 L150 1150 M830 842 L852 1150", 3.2) +
    inkPath("M170 842 L60 1170 M830 842 L940 1170", 1.4) +
    inkPath("M170 842 C380 900 620 900 830 842", 1.4) +
    g({ filter: "url(#cs-rough)" }, bulbs) +
    // 保冷箱＋紙箱
    shape("M720 1110 L840 1110 L836 1180 L724 1180 Z", "#5fa38a", 2.4, "cs-hatch-fine") +
    shape("M716 1096 L844 1096 L844 1112 L716 1112 Z", "#f3e9d2", 2.2) +
    shape("M600 1140 L690 1140 L690 1200 L600 1200 Z", "#c99a63", 2.2) +
    inkPath("M600 1160 L690 1160 M645 1140 L645 1160", 1.4) +
    // 營火底座（火焰是角色）
    g(
      { transform: "translate(560 1260)" },
      stones,
      fillPath("M-52 2 L44 -18 L50 -6 L-46 12 Z", "#6b4323"),
      fillPath("M-50 -18 L46 2 L40 12 L-54 -6 Z", "#7a4d29"),
      inkPath("M-52 2 L44 -18 L50 -6 L-46 12 Z M-50 -18 L46 2 L40 12 L-54 -6 Z", 2.4),
    ) +
    frontGrass(r) +
    flowers(r, 0, 1000, 1320, 1390, 10)
  );
}

/** 星空吊床：兩棵松樹之間掛吊床（吊床是角色會晃），旁邊小帳篷從裡面透出燈光。 */
function nightPanel(): string {
  const r = rng(707);
  return (
    meadowBase(r) +
    pine(250, 1150, 2.2, true) +
    pine(760, 1150, 2.1, true) +
    // 小帳篷（裡面點著營燈，布透出暖光；光暈在燈光層）
    shadow(915, 1224, 110, 10, 0.25) +
    fillPath("M900 1080 L936 1080 L1004 1220 L980 1220 Z", "#c98f3c") +
    h("path", { d: "M900 1080 L936 1080 L1004 1220 L980 1220 Z", fill: "url(#cs-hatch)", filter: "url(#cs-rough)" }) +
    fillPath("M820 1220 L900 1080 L980 1220 Z", "#f3c872") +
    fillPath("M872 1220 L900 1140 L928 1220 Z", "#fff0b0") +
    fillPath("M864 1220 C872 1190 884 1162 900 1140 C890 1168 884 1196 882 1220 Z", "#e2b24a") +
    h("circle", { cx: 902, cy: 1196, r: 7, fill: "#f7a93c" }) +
    inkPath("M820 1220 L900 1080 L980 1220 M900 1080 L936 1080 L1004 1220 L980 1220 M872 1220 L900 1140 L928 1220 M864 1220 C872 1190 884 1162 900 1140", 2.4) +
    inkPath("M852 1164 L900 1090 M948 1164 L906 1090", 1.3, { "stroke-dasharray": "5 5", opacity: 0.6 }) +
    inkPath("M900 1080 L900 1058 M820 1220 L792 1236 M936 1080 L1030 1226", 1.3) +
    inkPath("M788 1230 l4 12 M1026 1220 l4 12", 2.4) +
    shape("M60 1240 L180 1240 L176 1262 L64 1262 Z", "#7a4d29", 2.2) +
    bush(120, 1236, 0.7) +
    frontGrass(r) +
    flowers(r, 0, 1000, 1320, 1390, 8)
  );
}

/** 第 index 格的靜態內容（區域座標 0–1000）。 */
export function panelArt(index: number): string {
  if (index <= 2) return drivePanel(index);
  if (index === 3) return campsitePanel();
  if (index === 4) return coffeePanel();
  if (index === 5) return cliffBack();
  if (index === 6) return eveningPanel();
  return nightPanel();
}
