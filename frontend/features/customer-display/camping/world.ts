// 把整個世界組成一段 HTML：天空（固定）、遠山／林線（視差）、地面八格＋角色（跟鏡頭走）、
// 天色濾色、夜空與燈光、開場的門、紙紋。每個會動的東西各自一個 <div>（瀏覽器可把它當獨立圖層合成），
// 平移時不必重畫手繪濾鏡，平板才跑得動。
import { DEFS } from "./defs";
import {
  backViewPerson,
  camperVan,
  crouchingPerson,
  flame,
  hammock,
  MUG_IN_HAND,
  roastingPerson,
  sittingPerson,
  standingPerson,
  TENT_BAG,
  tent,
} from "./figures";
import { cloud, flowers, pine, scribbleSun } from "./nature";
import { PANEL_COUNT, PANEL_W, STAGE_H, cliffFront, panelArt } from "./panels";
import { povDesk } from "./props";
import {
  INK,
  LOGO_MARK,
  brandMark,
  doodleStar,
  doodleText,
  fillPath,
  g,
  h,
  hatchArea,
  inkPath,
  rng,
  shape,
} from "./svg";

export const PARALLAX = { far: 0.25, mid: 0.55 } as const;
/** 「謝謝光臨」的基線高度（時間軸歸位用）。 */
export const THANKS_Y = 360;
/** 鏡頭最右停在最後一格。 */
export const CAM_MAX = (PANEL_COUNT - 1) * PANEL_W;

/** 世界座標裡各場景的關鍵位置（x 為世界座標）。 */
export const SPOTS = {
  vanStartX: 380,
  vanParkX: 3380,
  // 帳篷右下角（含營繩營釘）要停在木棧台左緣之前
  tentX: 4180,
  sitX: 4560,
  cliffX: 5270,
  roastX: 6420,
  eveningFireX: 6560,
  coffeeFireX: 4330,
  hammockX: 7505,
  /** 雲海的流動雲只露在兩片崖之間。 */
  seaClipX: 5120,
  seaClipW: 760,
} as const;

type Box = { ox: number; oy: number; w: number; h: number };

/** 一個角色：div 平移到世界座標 (x,y) 時，svg 原點就落在那裡。 */
function actor(cls: string, box: Box, content: string, extraStyle = ""): string {
  return `<div class="cs-actor ${cls}" data-ox="${box.ox}" data-oy="${box.oy}" style="width:${box.w}px;height:${box.h}px;${extraStyle}"><svg viewBox="${-box.ox} ${-box.oy} ${box.w} ${box.h}" width="${box.w}" height="${box.h}" overflow="visible">${content}</svg></div>`;
}

function tileSvg(x: number, w: number, content: string, cls = "cs-tile"): string {
  return `<div class="${cls}" style="left:${x}px;width:${w}px"><svg viewBox="${x} 0 ${w} ${STAGE_H}" width="${w}" height="${STAGE_H}">${content}</svg></div>`;
}

/** 一條山脈：山脊點、色塊、背光面、岩壁筆觸、（可選）積雪。 */
function range(r: ReturnType<typeof rng>, width: number, base: number, peakMin: number, peakVar: number, colors: { body: string; face: string; ink: string; stroke: string }, snowBelow: number): string {
  const peaks: [number, number][] = [[-40, base - 60]];
  let x = -40;
  while (x < width + 200) {
    x += 110 + r() * 150;
    peaks.push([x, peakMin + r() * peakVar]);
    x += 70 + r() * 100;
    peaks.push([x, peakMin + peakVar * 0.8 + r() * 70]);
  }
  // 山脊不要是直線：每段再切幾個點、上下抖一點，像手順著山勢畫出來
  const detailed: [number, number][] = [];
  for (let i = 0; i < peaks.length - 1; i += 1) {
    const [ax, ay] = peaks[i] ?? [0, 0];
    const [bx, by] = peaks[i + 1] ?? [0, 0];
    const len = Math.hypot(bx - ax, by - ay);
    for (let k = 0; k < 6; k += 1) {
      const t = k / 6;
      const jitter = k === 0 ? 0 : (r() - 0.5) * len * 0.07;
      detailed.push([ax + (bx - ax) * t, ay + (by - ay) * t + jitter]);
    }
  }
  detailed.push(peaks[peaks.length - 1] ?? [0, 0]);
  const ridge = detailed.map(([px, py]) => `${px.toFixed(0)} ${py.toFixed(0)}`).join(" L");
  let faces = "";
  let snow = "";
  let strokes = "";
  for (let i = 1; i < peaks.length - 1; i += 2) {
    const [px, py] = peaks[i] ?? [0, 0];
    const [nx, ny] = peaks[i + 1] ?? [0, 0];
    // 每座山的背光面切法不同：有的整片、有的只到半山腰、有的是一道斜谷
    const kind = Math.floor(r() * 3);
    if (kind === 0) faces += `M${px.toFixed(0)} ${py.toFixed(0)} L${nx.toFixed(0)} ${ny.toFixed(0)} L${(px + (nx - px) * 0.35).toFixed(0)} ${(base + 20).toFixed(0)} L${(px + 6).toFixed(0)} ${(base + 20).toFixed(0)} Z `;
    else if (kind === 1) faces += `M${px.toFixed(0)} ${py.toFixed(0)} L${(px + (nx - px) * 0.7).toFixed(0)} ${(py + (ny - py) * 0.7).toFixed(0)} C${(px + 30).toFixed(0)} ${((py + base) / 2).toFixed(0)} ${(px + 14).toFixed(0)} ${((py + base) / 2 + 30).toFixed(0)} ${(px + 4).toFixed(0)} ${(base - 30).toFixed(0)} Z `;
    else faces += `M${(px + 2).toFixed(0)} ${(py + 8).toFixed(0)} C${(px + 22).toFixed(0)} ${(py + 60).toFixed(0)} ${(px + 40).toFixed(0)} ${(py + 110).toFixed(0)} ${(px + 70).toFixed(0)} ${(base + 20).toFixed(0)} L${(px + 20).toFixed(0)} ${(base + 20).toFixed(0)} C${(px + 14).toFixed(0)} ${(py + 120).toFixed(0)} ${(px + 6).toFixed(0)} ${(py + 60).toFixed(0)} ${(px + 2).toFixed(0)} ${(py + 8).toFixed(0)} Z `;
    if (py < snowBelow) {
      // 積雪每座不同：大小、左右垂下的長度都隨機
      const a = 16 + r() * 16;
      const b = 12 + r() * 14;
      snow += `M${px.toFixed(0)} ${py.toFixed(0)} l${a.toFixed(0)} ${(a * 1.2).toFixed(0)} l${(-a * 0.35).toFixed(0)} ${(-3 + r() * 4).toFixed(0)} l${(-a * 0.3).toFixed(0)} ${(8 + r() * 12).toFixed(0)} l${(-a * 0.3).toFixed(0)} ${(-10 - r() * 6).toFixed(0)} l${(-b * 0.5).toFixed(0)} ${(4 + r() * 6).toFixed(0)} l${(-b * 0.5).toFixed(0)} ${(-b * 0.6).toFixed(0)} Z `;
    }
    for (let k = 0; k < 9; k += 1) {
      const t = 0.1 + r() * 0.8;
      const sx = px + (nx - px) * t * 0.6 + 4;
      const sy = py + (base - py) * t * 0.7;
      strokes += `M${sx.toFixed(0)} ${sy.toFixed(0)} l${(6 + r() * 10).toFixed(0)} ${(10 + r() * 12).toFixed(0)} `;
    }
  }
  const body = `M${ridge} L${(x + 40).toFixed(0)} ${base + 120} L-40 ${base + 120} Z`;
  return (
    h("path", { d: body, fill: colors.body, filter: "url(#cs-pencil-soft)" }) +
    h("path", { d: faces, fill: colors.face, opacity: 0.8 }) +
    h("path", { d: strokes, stroke: colors.stroke, "stroke-width": 1, fill: "none", opacity: 0.55, "stroke-linecap": "round" }) +
    (snow ? h("path", { d: snow, fill: "#eef2f4" }) + h("path", { d: snow, fill: "none", stroke: colors.stroke, "stroke-width": 0.9, opacity: 0.7 }) : "") +
    h("path", { d: `M${ridge}`, fill: "none", stroke: colors.ink, "stroke-width": 1.5, "stroke-linejoin": "round", filter: "url(#cs-rough)" })
  );
}

/** 遠山：最遠一層淡藍灰、有雪；前一層略深帶綠；兩層山腳各有一片霧，越遠越淡、細節越少。 */
function farLayer(): string {
  const width = PANEL_W + CAM_MAX * PARALLAX.far + 100;
  const r = rng(41);
  const art =
    range(r, width, 900, 560, 110, { body: "#d3dade", face: "#c9d1d5", ink: "#bcc4c9", stroke: "#c3cbcf" }, 640) +
    h("rect", { x: -40, y: 760, width: width + 80, height: 260, fill: "url(#cs-haze)" }) +
    range(r, width, 960, 700, 70, { body: "#a9b6b0", face: "#98a69f", ink: "#7f8b86", stroke: "#86928d" }, 0) +
    h("rect", { x: -40, y: 850, width: width + 80, height: 200, fill: "url(#cs-haze)" });
  let tiles = "";
  for (let tx = 0; tx < width; tx += PANEL_W) tiles += tileSvg(tx, Math.min(PANEL_W, width - tx), art);
  return `<div class="cs-layer cs-far">${tiles}</div>`;
}

/** 林線：遠一排小而灰的樹、近一排中景的樹（高矮胖瘦各不同、成群有空隙），底下起伏的山丘帶。 */
function midLayer(): string {
  const width = PANEL_W + CAM_MAX * PARALLAX.mid + 100;
  const r = rng(77);
  let top = "M-20 950";
  for (let x = -20; x < width + 200; x += 250) top += ` C${x + 80} ${900 + r() * 30} ${x + 170} ${930 + r() * 30} ${x + 250} ${940 + r() * 20}`;
  const band = `${top} L${width + 250} 1130 L-20 1130 Z`;
  const farTrees: [number, number][] = [];
  const midTrees: [number, number][] = [];
  for (let x = 10; x < width; x += 14 + r() * 30) if (r() > 0.3) farTrees.push([x, 0.35 + r() * 0.25]);
  let x = 10;
  while (x < width) {
    // 一群 2～6 棵，群與群之間留空
    const count = 2 + Math.floor(r() * 5);
    for (let i = 0; i < count; i += 1) midTrees.push([x + i * (18 + r() * 16), 0.5 + r() * 0.45]);
    x += count * 26 + 60 + r() * 160;
  }
  let tiles = "";
  for (let tx = 0; tx < width; tx += PANEL_W) {
    const w = Math.min(PANEL_W, width - tx);
    let trees = "";
    for (const [px, sc] of farTrees) if (px > tx - 60 && px < tx + w + 60) trees += pine(px, 962, sc, false, "far");
    trees += h("rect", { x: tx - 60, y: 900, width: w + 120, height: 90, fill: "url(#cs-haze)", opacity: 0.7 });
    for (const [px, sc] of midTrees) if (px > tx - 60 && px < tx + w + 60) trees += pine(px, 995, sc, false, "mid");
    tiles += tileSvg(tx, w, h("path", { d: band, fill: "#8ea676", filter: "url(#cs-pencil-soft)" }) + h("path", { d: top, fill: "none", stroke: "#5f7452", "stroke-width": 1.4, filter: "url(#cs-rough)" }) + trees);
  }
  return `<div class="cs-layer cs-mid">${tiles}</div>`;
}

function groundTrack(): string {
  let tiles = "";
  for (let i = 0; i < PANEL_COUNT; i += 1) {
    tiles += `<div class="cs-tile" style="left:${i * PANEL_W}px;width:${PANEL_W}px"><svg viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}">${panelArt(i)}</svg></div>`;
  }
  const r = rng(909);
  let seaClouds = "";
  for (let i = 0; i < 4; i += 1) {
    seaClouds += actor(`cs-sea-cloud cs-sea-cloud-${i}`, { ox: 160, oy: 90, w: 320, h: 110 }, cloud(260 + r() * 60, "#fdfbf6", "#e9e6df"));
  }
  const items =
    actor("cs-item cs-item-cooler", { ox: 70, oy: 90, w: 140, h: 100 }, shape("M-56 -70 L56 -70 L52 0 L-52 0 Z", "#5fa38a", 2.4, "cs-hatch-fine") + shape("M-60 -84 L60 -84 L60 -68 L-60 -68 Z", "#f3e9d2", 2.2)) +
    actor("cs-item cs-item-box", { ox: 60, oy: 80, w: 120, h: 90 }, shape("M-44 -60 L44 -60 L44 0 L-44 0 Z", "#c99a63", 2.2) + inkPath("M-44 -40 L44 -40 M0 -60 L0 -40", 1.4)) +
    actor("cs-item cs-item-lantern", { ox: 30, oy: 70, w: 60, h: 80 }, shape("M-12 -44 L12 -44 L12 -8 L-12 -8 Z", "#f7d67a", 2) + shape("M-14 -8 L14 -8 L14 0 L-14 0 Z", "#3f6b52", 1.8) + inkPath("M-8 -44 C-8 -60 8 -60 8 -44", 2));
  return (
    `<div class="cs-layer cs-track">${tiles}` +
    `<div class="cs-sea-clip" style="left:${SPOTS.seaClipX}px;width:${SPOTS.seaClipW}px">${seaClouds}</div>` +
    `<div class="cs-tile" style="left:${5 * PANEL_W}px;width:${PANEL_W}px"><svg viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}">${cliffFront()}</svg></div>` +
    actor("cs-tent-actor", { ox: 320, oy: 420, w: 680, h: 480 }, tent()) +
    actor("cs-fire cs-fire-coffee", { ox: 60, oy: 180, w: 120, h: 200 }, flame()) +
    actor("cs-fire cs-fire-evening", { ox: 60, oy: 180, w: 120, h: 200 }, flame(0.9)) +
    items +
    actor("cs-van-actor", { ox: 280, oy: 300, w: 560, h: 330 }, camperVan()) +
    actor("cs-pose cs-walker", { ox: 80, oy: 230, w: 180, h: 260 }, standingPerson("cs-pose-walk", TENT_BAG + MUG_IN_HAND)) +
    actor("cs-pose cs-hammerer", { ox: 100, oy: 190, w: 260, h: 220 }, crouchingPerson()) +
    actor("cs-pose cs-sitter", { ox: 100, oy: 250, w: 220, h: 270 }, sittingPerson()) +
    actor("cs-pose cs-cliffsitter", { ox: 90, oy: 180, w: 200, h: 200 }, backViewPerson()) +
    actor("cs-pose cs-roaster", { ox: 80, oy: 190, w: 300, h: 210 }, roastingPerson()) +
    actor("cs-pose cs-hammock-actor", { ox: 280, oy: 60, w: 560, h: 220 }, hammock(470)) +
    // 營火的暖光：很淡地染到周圍的草、木棧台、帳篷與人（柔光混合，不是發光）
    actor("cs-fire-ambient", { ox: 340, oy: 260, w: 680, h: 420 }, h("ellipse", { cx: 0, cy: -20, rx: 330, ry: 200, fill: "url(#cs-ambient)" })) +
    `</div>`
  );
}

/** 天空（不跟鏡頭走）：漸層＋光帶；太陽、月亮、雲、鳥各自一層。 */
function skyLayers(): string {
  const r = rng(5);
  let clouds = "";
  const spots: [number, number, number][] = [[120, 300, 200], [620, 200, 240], [880, 420, 170], [360, 520, 150]];
  spots.forEach(([x, y, w], i) => {
    clouds += actor(`cs-cloud cs-cloud-${i}`, { ox: w / 2 + 10, oy: w * 0.45, w: w + 20, h: w * 0.5 }, cloud(w), `left:0;top:0`);
    void x;
    void y;
  });
  let birds = "";
  for (const [x, y, s] of [[0, 0, 1], [48, 30, 0.8], [-30, 50, 0.7]] as const) birds += `M${x} ${y} q${10 * s} -12 ${20 * s} 0 q${10 * s} -12 ${20 * s} 0 `;
  void r;
  return (
    `<div class="cs-layer cs-sky"><svg viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}" preserveAspectRatio="none">` +
    // 天空往兩側多鋪一些：結帳拉近鏡頭時左右不會露出邊
    h("rect", { x: -600, width: PANEL_W + 1200, height: STAGE_H, fill: "url(#cs-sky)" }) +
    g({ class: "cs-lightbands", opacity: 0.3 }, h("polygon", { points: "-200,0 60,0 1600,1245 1600,1540", fill: "#f4d9a8" }), h("polygon", { points: "160,0 330,0 1600,1020 1600,1240", fill: "#f4d9a8" })) +
    `</svg></div>` +
    // 天空的水彩暈染（靜態、不跟天色一起重畫）
    `<div class="cs-layer cs-skywash"><svg viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}" preserveAspectRatio="none">${h("rect", { x: -600, width: PANEL_W + 1200, height: 900, fill: "#fff", filter: "url(#cs-wash)" })}</svg></div>` +
    actor("cs-sun", { ox: 150, oy: 150, w: 300, h: 300 }, scribbleSun(rng(21))) +
    actor("cs-moon", { ox: 80, oy: 80, w: 160, h: 160 }, shape("M30 -54 C-20 -60 -56 -20 -50 24 C-44 60 0 76 36 56 C0 50 -22 20 -16 -12 C-10 -36 8 -50 30 -54 Z", "#f7ecc4", 2.6)) +
    clouds +
    actor("cs-balloon", { ox: 90, oy: 130, w: 180, h: 280 }, balloon()) +
    actor("cs-birds", { ox: 40, oy: 30, w: 140, h: 100 }, h("path", { d: birds, fill: "none", stroke: INK, "stroke-width": 2.4, "stroke-linecap": "round", filter: "url(#cs-rough)" })) +
    actor("cs-rainbow", { ox: 520, oy: 520, w: 1040, h: 560 }, g({ fill: "none", "stroke-linecap": "round", filter: "url(#cs-rough)" }, ...["#e8674a", "#f0a93b", "#f2d25a", "#7fb36a", "#6fa3cf", "#8a79c2"].map((c, i) => h("path", { class: "cs-rainbow-band", d: `M${-440 + i * 18} 0 A${440 - i * 18} ${420 - i * 18} 0 0 1 ${440 - i * 18} 0`, stroke: c, "stroke-width": 16, opacity: 0.85 }))))
  );
}

/** 熱氣球（球皮上印露坑 logo），原點在球皮中心：分片的球皮、背光面斜線、吊繩、編織吊籃。 */
function balloon(): string {
  const env = "M0 -110 C62 -110 88 -60 80 -10 C72 40 30 70 14 92 L-14 92 C-30 70 -72 40 -80 -10 C-88 -60 -62 -110 0 -110 Z";
  const gore = (a: number) => `M0 -110 C${f1(a * 0.8)} -100 ${f1(a * 1.1)} -50 ${f1(a)} -10 C${f1(a * 0.9)} 30 ${f1(a * 0.35)} 70 ${f1(a * 0.17)} 92`;
  let basket = "";
  for (let x = -12; x <= 12; x += 4) basket += `M${x} 120 L${x - 1} 138 `;
  return g(
    { class: "cs-balloon-bob" },
    g(
    { transform: "scale(0.82)" },
    fillPath(env, "#e36f4f"),
    fillPath("M0 -110 C30 -110 40 -60 36 -10 C32 40 16 70 8 92 L-8 92 C-16 70 -32 40 -36 -10 C-40 -60 -30 -110 0 -110 Z", "#f3e9d6"),
    hatchArea("M0 -110 C62 -110 88 -60 80 -10 C72 40 30 70 14 92 L8 92 C16 70 32 40 36 -10 C40 -60 30 -110 0 -110 Z", "cs-hatch-fine"),
    h("path", { d: `${gore(-60)} ${gore(-36)} ${gore(36)} ${gore(60)}`, fill: "none", stroke: "#8a3a26", "stroke-width": 0.9, opacity: 0.6 }),
    h("path", { d: "M-50 -80 C-60 -60 -62 -30 -58 -8", stroke: "#fff", "stroke-width": 4, fill: "none", opacity: 0.5, "stroke-linecap": "round" }),
    h("path", { d: env, fill: "url(#cs-fabric)", opacity: 0.8 }),
    brandMark("cs-balloon-mark", LOGO_MARK, -24, -44, 48, 41, "#3a2210"),
    inkPath(env, 2.2),
    inkPath("M-14 92 L-12 118 M14 92 L12 118 M-5 92 L-5 118 M5 92 L5 118", 1),
    shape("M-16 118 L16 118 L13 140 L-13 140 Z", "#b98352", 1.8),
    h("path", { d: basket + "M-15 126 L15 126 M-14 133 L14 133", stroke: "#6b4a2e", "stroke-width": 0.8, fill: "none" }),
    ),
  );
}

function f1(v: number): string {
  return (Math.round(v * 10) / 10).toString();
}

/** 夜空：星星（會閃）、流星、星座連成露坑 logo（三角箭頭＋樹）。 */
function nightSky(): string {
  const r = rng(88);
  let stars = "";
  for (let i = 0; i < 70; i += 1) {
    const x = r() * 1000;
    const y = 40 + r() * 760;
    stars += r() > 0.85 ? g({ class: "cs-star", transform: `translate(${x.toFixed(0)} ${y.toFixed(0)})` }, doodleStar(5 + r() * 4, "#fbeaa0")) : h("circle", { class: "cs-star", cx: x, cy: y, r: 1.4 + r() * 1.8, fill: "#fdf6d8" });
  }
  // 星座：logo 的三角箭頭與中間一棵樹（取 logo 輪廓的幾個關鍵點）
  const pts: [number, number][] = [[500, 220], [640, 470], [360, 470], [500, 220], [500, 300], [450, 400], [550, 400], [500, 300]];
  const path = `M${pts.map(([x, y]) => `${x} ${y}`).join(" L")}`;
  let nodes = "";
  for (const [x, y] of pts.slice(0, 7)) nodes += g({ class: "cs-const-star", transform: `translate(${x} ${y})` }, doodleStar(11, "#fff3b8"));
  return (
    `<div class="cs-layer cs-night"><svg viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}">` +
    stars +
    h("path", { class: "cs-shooting", d: "M760 120 L640 200", stroke: "#fff7d0", "stroke-width": 3, "stroke-linecap": "round", opacity: 0 }) +
    g({ class: "cs-constellation" }, h("path", { class: "cs-const-line", d: path, fill: "none", stroke: "#fff3b8", "stroke-width": 2.4, "stroke-dasharray": "4 8", "stroke-linecap": "round", opacity: 0.9 }), nodes) +
    g({ class: "cs-night-word", transform: "translate(500 590)" }, doodleText("露坑", { font: "marker", size: 120, fill: "#fff1d6", outline: "#1a2340", outlineWidth: 12, drop: 6 })) +
    `</svg></div>`
  );
}

/** 燈光層（在天色濾色之上，跟地面一起走）：營燈、營火、串燈、帳篷燈、螢火蟲。 */
function lightsTrack(): string {
  const glow = (x: number, y: number, rr: number, cls: string) => h("circle", { class: cls, cx: x, cy: y, r: rr, fill: "url(#cs-glow)" });
  let bulbs = "";
  for (let i = 0; i < 9; i += 1) {
    const t = i / 8;
    bulbs += glow(6170 + t * 640, 854 + Math.sin(t * Math.PI) * 44, 34, "cs-bulb-glow");
  }
  const r = rng(12);
  let flies = "";
  for (let i = 0; i < 16; i += 1) flies += h("circle", { class: "cs-firefly", cx: 7050 + r() * 900, cy: 950 + r() * 330, r: 9, fill: "url(#cs-firefly)" });
  return (
    `<div class="cs-layer cs-lights"><svg viewBox="0 0 ${PANEL_COUNT * PANEL_W} ${STAGE_H}" width="${PANEL_COUNT * PANEL_W}" height="${STAGE_H}">` +
    g({ class: "cs-lights-evening" }, glow(4700, 980, 90, "cs-lantern-glow"), glow(SPOTS.coffeeFireX, 1200, 200, "cs-fire-glow"), glow(SPOTS.eveningFireX, 1210, 230, "cs-fire-glow"), bulbs, glow(7900, 1180, 140, "cs-tentlamp-glow")) +
    g({ class: "cs-fireflies" }, flies) +
    `</svg></div>`
  );
}

function rainLayer(): string {
  const r = rng(33);
  let drops = "";
  for (let i = 0; i < 120; i += 1) {
    const x = r() * 1100 - 50;
    const y = r() * 1400;
    drops += `M${x.toFixed(0)} ${y.toFixed(0)} l-8 26 `;
  }
  // 兩份疊起來往下捲，接縫看不出來
  const sheet = inkPath(drops, 2, { stroke: "#6f8fae", opacity: 0.7, filter: undefined });
  return `<div class="cs-layer cs-rain"><svg viewBox="0 0 ${PANEL_W} ${STAGE_H * 2}" width="${PANEL_W}" height="${STAGE_H * 2}">${sheet}${g({ transform: `translate(0 ${STAGE_H})` }, sheet)}</svg></div>`;
}

/** 櫥窗：玻璃、反光、裡面擺露營用品。 */
function shopWindow(x: number, y: number, w: number, hgt: number, left: boolean): string {
  const glass = `M${x} ${y} L${x + w} ${y} L${x + w} ${y + hgt} L${x} ${y + hgt} Z`;
  const shelfY = y + hgt * 0.62;
  const goods = left
    ? // 左窗：小帳篷、營燈（亮著）、手沖壺
      g({ transform: `translate(${x + w * 0.3} ${shelfY})` }, shape("M-50 0 L0 -80 L50 0 Z", "#dca64a", 2.2), shape("M-12 0 L0 -30 L12 0 Z", "#4a3322", 1.6)) +
      h("circle", { cx: x + w * 0.72, cy: shelfY - 40, r: 46, fill: "url(#cs-glow)" }) +
      g({ transform: `translate(${x + w * 0.72} ${shelfY})` }, shape("M-12 -44 L12 -44 L12 -8 L-12 -8 Z", "#f7d67a", 2), shape("M-14 -8 L14 -8 L14 0 L-14 0 Z", "#3f6b52", 1.8), inkPath("M-8 -44 C-8 -60 8 -60 8 -44", 2)) +
      g({ transform: `translate(${x + w * 0.5} ${y + hgt * 0.3})` }, shape("M-20 20 L20 20 L16 -20 L-16 -20 Z", "#d9dbd6", 2), inkPath("M16 -8 C36 -16 40 -30 48 -36", 2))
    : // 右窗：一疊杯子、盆栽、咖啡豆罐
      g({ transform: `translate(${x + w * 0.3} ${shelfY})` }, shape("M-22 0 L22 0 L20 -30 L-20 -30 Z", "#f4efe4", 2), shape("M-22 -30 L22 -30 L20 -60 L-20 -60 Z", "#f4efe4", 2), inkPath("M22 -50 c10 0 10 12 0 12 M22 -20 c10 0 10 12 0 12", 1.8), g({ transform: "translate(0 -18) scale(0.9)" }, h("use", { href: "#cs-mug-mark" }))) +
      g({ transform: `translate(${x + w * 0.72} ${shelfY})` }, shape("M-24 0 L24 0 L20 -34 L-20 -34 Z", "#c9553a", 2), shape("M0 -34 C-30 -60 -20 -90 0 -96 C20 -90 30 -60 0 -34 Z", "#6f8f4c", 2, "cs-hatch-fine")) +
      g({ transform: `translate(${x + w * 0.5} ${y + hgt * 0.3})` }, shape("M-18 24 L18 24 L18 -20 L-18 -20 Z", "#8a5a34", 2), shape("M-20 -28 L20 -28 L20 -20 L-20 -20 Z", "#3a2a20", 1.8));
  return (
    shape(`M${x - 16} ${y - 16} L${x + w + 16} ${y - 16} L${x + w + 16} ${y + hgt + 16} L${x - 16} ${y + hgt + 16} Z`, "#3f6b52", 2.8) +
    fillPath(glass, "#fbeed0") +
    goods +
    shape(`M${x} ${shelfY} L${x + w} ${shelfY} L${x + w} ${shelfY + 10} L${x} ${shelfY + 10} Z`, "#8a5a34", 1.6) +
    h("path", { d: `M${x + 20} ${y + 70} L${x + 90} ${y} M${x + 30} ${y + 120} L${x + 150} ${y} M${x + w - 60} ${y + hgt} L${x + w} ${y + hgt - 70}`, stroke: "#fff", "stroke-width": 7, "stroke-linecap": "round", opacity: 0.55 }) +
    inkPath(glass, 2.4) +
    shape(`M${x - 24} ${y + hgt + 16} L${x + w + 24} ${y + hgt + 16} L${x + w + 16} ${y + hgt + 30} L${x - 16} ${y + hgt + 30} Z`, "#8a5a34", 2)
  );
}

/** 開場：露坑店門口，鏡頭推近，一隻手推開門走進去。門用 CSS 3D 轉開。 */
function doorIntro(): string {
  const r = rng(2);
  let boards = "";
  for (let y = 0; y < 1400; y += 40) {
    boards += h("rect", { x: 0, y, width: 1000, height: 40, fill: r() > 0.5 ? "#d8bf97" : "#d2b78d" });
  }
  let boardLines = "";
  for (let y = 40; y < 1400; y += 40) boardLines += `M0 ${y} L1000 ${y} `;
  for (let i = 0; i < 40; i += 1) {
    const x = r() * 980;
    const y = Math.floor(r() * 35) * 40 + 12 + r() * 14;
    boardLines += `M${x.toFixed(0)} ${y.toFixed(0)} q30 -3 ${(40 + r() * 50).toFixed(0)} 0 `;
  }
  let stripes = "";
  for (let i = 0; i < 12; i += 1) {
    const x0 = 110 + i * 65;
    stripes += fillPath(`M${x0} 330 L${x0 + 32.5} 330 L${x0 + 36} 420 L${x0 + 2} 420 Z`, "#3f6b52");
  }
  let scallop = "M110 420";
  for (let i = 0; i < 12; i += 1) scallop += ` q16.25 34 32.5 0 q16.25 34 32.5 0`;
  const lamp = (x: number) =>
    h("circle", { cx: x, cy: 560, r: 90, fill: "url(#cs-glow)" }) +
    shape(`M${x - 18} 540 L${x + 18} 540 L${x + 12} 590 L${x - 12} 590 Z`, "#f7d67a", 2.2) +
    shape(`M${x - 24} 530 L${x + 24} 530 L${x + 18} 540 L${x - 18} 540 Z M${x - 6} 590 L${x + 6} 590 L${x + 6} 600 L${x - 6} 600 Z`, "#3a3330", 2) +
    inkPath(`M${x} 530 L${x} 500 L${x - 30} 500`, 2.4);
  const planter = (x: number, flip: number) =>
    g(
      { transform: `translate(${x} 1250) scale(${flip} 1)` },
      shape("M-80 0 L80 0 L70 90 L-70 90 Z", "#b98352", 2.6, "cs-hatch-fine"),
      inkPath("M-80 30 L80 30", 1.6),
      shape("M-60 0 C-80 -60 -40 -110 -10 -90 C0 -140 60 -130 56 -80 C90 -70 80 -10 60 0 Z", "#6f8f4c", 2.4, "cs-hatch-fine"),
      flowers(rng(40 + x), -60, 60, -90, -10, 7),
    );
  const board = "M230 120 L770 120 C784 120 790 128 790 140 L790 280 C790 292 784 300 770 300 L230 300 C216 300 210 292 210 280 L210 140 C210 128 216 120 230 120 Z";
  const wall =
    boards +
    h("path", { d: boardLines, fill: "none", stroke: INK, "stroke-width": 1.2, opacity: 0.35, filter: "url(#cs-rough)" }) +
    // 掛牌招牌（鏈子吊著）
    inkPath("M300 60 L300 120 M700 60 L700 120", 2.6, { "stroke-dasharray": "8 5" }) +
    shape(board, "#8a5a34", 3.2, "cs-hatch-fine") +
    inkPath("M240 150 q60 -4 120 0 M620 270 q60 3 120 0", 1.2, { opacity: 0.4 }) +
    brandMark("cs-door-mark", LOGO_MARK, 300, 158, 116, 99, "#fff1d6") +
    g({ transform: "translate(580 250)" }, doodleText("露坑", { font: "marker", size: 104, fill: "#fff1d6", outline: "#2a1a0c", outlineWidth: 11, drop: 5 })) +
    // 條紋雨遮
    fillPath("M110 330 L890 330 L890 420 L110 420 Z", "#f3e9d2") +
    stripes +
    fillPath(`${scallop} L890 420 L890 400 L110 400 Z`, "#f3e9d2") +
    inkPath("M100 330 L900 330 M110 330 L110 420 M890 330 L890 420", 3) +
    inkPath(scallop, 2.6) +
    shopWindow(50, 520, 210, 460, true) +
    shopWindow(740, 520, 210, 460, false) +
    lamp(280) +
    lamp(720) +
    // 門框、門內的光（門轉開後看得到）
    shape("M300 450 L700 450 L700 1400 L300 1400 Z", "#5a3b24", 3) +
    // 門口地墊
    shape("M320 1360 L680 1360 L700 1400 L300 1400 Z", "#c9553a", 2.4, "cs-hatch-fine") +
    planter(150, 1) +
    planter(850, -1) +
    // 黑板立牌
    g(
      { transform: "translate(860 1330) rotate(4)" },
      inkPath("M-60 70 L-40 -120 L40 -120 L60 70", 3),
      shape("M-50 -110 L50 -110 L44 20 L-44 20 Z", "#2f3a33", 2.6),
      g({ transform: "translate(0 -46)" }, doodleText("咖啡", { font: "round", size: 42, fill: "#f7f2e6", outline: "#2f3a33", outlineWidth: 2, drop: 0, texture: "none" })),
      inkPath("M-18 -20 L18 -20 L14 4 L-14 4 Z M18 -14 c8 0 8 10 0 10", 2, { stroke: "#f7f2e6" }),
    );
  const doorArt =
    shape("M0 0 L360 0 L360 930 L0 930 Z", "#b98352", 3) +
    h("path", { d: "M20 20 L340 20 L340 910 L20 910 Z", fill: "url(#cs-hatch-fine)", opacity: 0.45, filter: "url(#cs-rough)" }) +
    shape("M50 50 L310 50 L310 400 L50 400 Z", "#fbeed0", 2.6) +
    h("path", { d: "M70 150 L160 60 M80 230 L230 80", stroke: "#fff", "stroke-width": 8, "stroke-linecap": "round", opacity: 0.6 }) +
    inkPath("M180 50 L180 400 M50 225 L310 225", 3) +
    shape("M50 470 L310 470 L310 860 L50 860 Z", "#c99a63", 2.4) +
    inkPath("M70 490 L290 490 L290 840 L70 840 Z", 1.4, { opacity: 0.6 }) +
    // 門上掛牌
    inkPath("M130 300 L180 260 L230 300", 2) +
    shape("M100 300 L260 300 L260 380 L100 380 Z", "#f3e9d2", 2.4) +
    g({ transform: "translate(180 362)" }, doodleText("歡迎", { font: "round", size: 52, fill: "#e8674a", outlineWidth: 6, drop: 3, texture: "none" })) +
    // 門把（拉桿）
    shape("M300 440 L340 440 L340 500 L300 500 Z", "#e2b24a", 2.2) +
    shape("M296 462 L346 462 L346 478 L296 478 Z", "#c89a36", 2.2);
  // 握住門把的手：從右下伸進來的袖子＋拳頭（四根指節在左、大拇指壓在門把上），門把橫穿過拳頭 y=0
  const hand =
    shape("M34 -20 L170 96 L128 150 L30 22 Z", "#c2643f", 3, "cs-hatch") +
    shape("M30 -22 L50 -6 L40 30 L22 22 Z", "#a8502f", 2.4) +
    shape("M-6 -24 C-16 -24 -19 -15 -12 -12 C-21 -9 -21 1 -12 2 C-21 5 -20 14 -11 15 C-17 19 -13 27 -4 27 L24 28 C38 28 42 16 40 0 C38 -16 32 -26 20 -26 Z", "#efc9a6", 2.6) +
    inkPath("M-12 -12 C-4 -12 2 -11 6 -10 M-12 2 C-4 2 2 2 8 3 M-11 15 C-4 15 2 15 6 14", 1.6, { opacity: 0.75 }) +
    shape("M18 -24 C10 -34 -6 -36 -16 -30 C-22 -26 -18 -20 -10 -21 C0 -22 8 -20 14 -16 Z", "#efc9a6", 2.4);
  return (
    `<div class="cs-door"><svg class="cs-door-wall" viewBox="0 0 1000 1400" width="1000" height="1400">${wall}</svg>` +
    `<div class="cs-door-light"></div>` +
    `<div class="cs-door-leaf"><svg viewBox="-6 -6 372 942" width="372" height="942">${doorArt}</svg></div>` +
    `<div class="cs-door-hand"><svg viewBox="-40 -60 240 240" width="240" height="240">${hand}</svg></div>` +
    `</div>`
  );
}

/** 結帳完成／簽署完成的謝謝（塗鴉泡泡字＋星星），畫在天空位置。 */
function thanksLayer(): string {
  let stars = "";
  const spots: [number, number, number][] = [[210, 250, 18], [800, 230, 22], [150, 470, 12], [860, 480, 14], [500, 140, 16]];
  for (const [x, y, s] of spots) stars += g({ class: "cs-thanks-star", transform: `translate(${x} ${y})` }, doodleStar(s, "#f0c43b"));
  return (
    `<div class="cs-layer cs-thanks"><svg viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}">` +
    stars +
    g({ class: "cs-thanks-word", transform: `translate(500 ${THANKS_Y})` }, doodleText("謝謝光臨", { font: "round", size: 150, fill: "#f0a93b", outlineWidth: 18, drop: 9, texture: "hatch" })) +
    `</svg></div>`
  );
}

/** 整個場景的 HTML。 */
export function buildSceneHtml(): string {
  const defs = DEFS + `<symbol id="cs-mug-mark" overflow="visible">${brandMark("cs-mug-mask", LOGO_MARK, -12, -10, 24, 21, "#4a2c16")}</symbol>`;
  return (
    `<svg class="cs-defs" width="0" height="0" aria-hidden="true"><defs>${defs}</defs></svg>` +
    `<div class="cs-stage"><div class="cs-zoom">` +
    skyLayers() +
    farLayer() +
    midLayer() +
    groundTrack() +
    `<div class="cs-layer cs-tint"></div>` +
    nightSky() +
    `<div class="cs-layer cs-lights-wrap">${lightsTrack()}</div>` +
    rainLayer() +
    `</div>` +
    `<div class="cs-layer cs-pov"><svg viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}">${povDesk(rng(515))}</svg></div>` +
    thanksLayer() +
    doorIntro() +
    `<div class="cs-flash"></div>` +
    `<svg class="cs-paper" viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}" preserveAspectRatio="none">${h("rect", { width: PANEL_W, height: STAGE_H, fill: "#fff", "fill-opacity": 0.01, filter: "url(#cs-paper)" })}</svg>` +
    `</div>`
  );
}
