// 把整個世界組成一段 HTML：天空（固定）、遠山／林線（視差）、地面七格＋角色（跟鏡頭走）、
// 天色濾色、夜空與燈光、紙紋。每個會動的東西各自一個 <div>（瀏覽器可把它當獨立圖層合成），
// 平移時不必重畫手繪濾鏡，平板才跑得動。
import { DEFS } from "./defs";
import {
  backViewPerson,
  camperVan,
  crouchingPerson,
  flame,
  heater,
  MUG_IN_HAND,
  tarp,
  tarpBulbs,
  roastingPerson,
  sittingPerson,
  standingPerson,
  TENT_BAG,
  tent,
} from "./figures";
import { cloud, pine, scribbleSun } from "./nature";
import { PANEL_COUNT, PANEL_W, STAGE_H, cliffFront, panelArt } from "./panels";
import { campingWagon, electricPump, ledLantern, powerStation, povDesk } from "./props";
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
  vanStartX: 2760,
  vanParkX: 3380,
  // 帳篷右下角（含營繩營釘）要停在木棧台左緣之前
  tentX: 4180,
  sitX: 4560,
  coffeeFireX: 4330,
  /** 天幕營位（第 5 格）：天幕、暖爐、營火、坐的木頭。 */
  tarpX: 5460,
  tarpY: 1175,
  heaterX: 5330,
  eveningFireX: 5620,
  roastX: 5470,
  /** 雲海（第 6 格）。 */
  cliffX: 6270,
  /** 雲海的流動雲只露在兩片崖之間。 */
  seaClipX: 6000,
  seaClipW: 1000,
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
    // 前後層：前面的大、亮、清楚；後面的小、灰、淡，四種輪廓輪流用
    const back = i % 2 === 1;
    seaClouds += actor(`cs-sea-cloud cs-sea-cloud-${i}`, { ox: 160, oy: 90, w: 320, h: 110 }, g({ opacity: back ? 0.65 : 1 }, cloud(back ? 190 + r() * 40 : 260 + r() * 60, back ? "#f1efea" : "#fdfbf6", back ? "#dedcdc" : "#e9e6df", i + 1)));
  }
  const items =
    actor("cs-wagon-actor", { ox: 100, oy: 140, w: 250, h: 160 }, campingWagon()) +
    actor("cs-pump-actor", { ox: 30, oy: 50, w: 190, h: 90 }, electricPump()) +
    actor("cs-item cs-power-actor", { ox: 70, oy: 100, w: 170, h: 140 }, powerStation()) +
    actor("cs-item cs-led-actor", { ox: 100, oy: 100, w: 200, h: 160 }, ledLantern()) +
    actor("cs-item cs-power-cable", { ox: 0, oy: 0, w: 130, h: 160 }, inkPath("M60 0 C100 0 104 -18 90 -34 L12 -142", 2.2, { stroke: "#394b48" }));
  return (
    `<div class="cs-layer cs-track">${tiles}` +
    `<div class="cs-sea-clip" style="left:${SPOTS.seaClipX}px;width:${SPOTS.seaClipW}px">${seaClouds}</div>` +
    `<div class="cs-tile" style="left:${6 * PANEL_W}px;width:${PANEL_W}px"><svg viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}">${cliffFront()}</svg></div>` +
    actor("cs-tarp-actor", { ox: 500, oy: 380, w: 1000, h: 460 }, tarp()) +
    actor("cs-heater-actor", { ox: 40, oy: 110, w: 80, h: 120 }, heater()) +
    // 雨後地上的濕：幾灘水窪反光（天幕營位附近）
    actor("cs-wet", { ox: 500, oy: 60, w: 1000, h: 200 }, h("ellipse", { cx: -250, cy: 40, rx: 70, ry: 9, fill: "#b8cad6", opacity: 0.55 }) + h("ellipse", { cx: 60, cy: 90, rx: 90, ry: 11, fill: "#b8cad6", opacity: 0.5 }) + h("ellipse", { cx: 330, cy: 55, rx: 55, ry: 7, fill: "#b8cad6", opacity: 0.5 }) + inkPath("M-290 38 q20 -3 40 0 M30 88 q30 -3 60 0", 1, { stroke: "#fff", opacity: 0.8, filter: undefined }), "opacity:0") +
    actor("cs-tent-actor", { ox: 320, oy: 420, w: 680, h: 480 }, tent()) +
    actor("cs-fire cs-fire-coffee", { ox: 60, oy: 180, w: 120, h: 200 }, flame()) +
    actor("cs-fire cs-fire-evening", { ox: 60, oy: 180, w: 120, h: 200 }, flame(0.9)) +
    actor("cs-van-actor", { ox: 280, oy: 300, w: 560, h: 330 }, camperVan()) +
    items +
    actor("cs-pose cs-walker", { ox: 80, oy: 230, w: 180, h: 260 }, standingPerson("cs-pose-walk", TENT_BAG + MUG_IN_HAND)) +
    actor("cs-pose cs-hammerer", { ox: 100, oy: 190, w: 260, h: 220 }, crouchingPerson()) +
    actor("cs-pose cs-sitter", { ox: 100, oy: 250, w: 220, h: 270 }, sittingPerson()) +
    actor("cs-pose cs-cliffsitter", { ox: 90, oy: 180, w: 200, h: 200 }, backViewPerson()) +
    actor("cs-pose cs-roaster", { ox: 80, oy: 190, w: 300, h: 210 }, roastingPerson()) +
    // 營火的暖光：很淡地染到周圍的草、木棧台、帳篷與人（柔光混合，不是發光）
    actor("cs-fire-ambient", { ox: 340, oy: 260, w: 680, h: 420 }, h("ellipse", { cx: 0, cy: -20, rx: 330, ry: 200, fill: "url(#cs-ambient)" })) +
    // 傍晚營火、暖爐與串燈、夜裡帳篷的暖光：同樣用柔光很淡地染到周圍的草、布、人
    actor("cs-evening-ambient", { ox: 500, oy: 300, w: 1000, h: 460 }, h("ellipse", { cx: 150, cy: 0, rx: 360, ry: 200, fill: "url(#cs-ambient)" }) + h("ellipse", { cx: -140, cy: -80, rx: 300, ry: 190, fill: "url(#cs-ambient)" }), "opacity:0") +
    actor("cs-tent-ambient", { ox: 400, oy: 300, w: 800, h: 460 }, h("ellipse", { cx: 0, cy: -60, rx: 360, ry: 230, fill: "url(#cs-ambient)" }), "opacity:0") +
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
    actor("cs-rainbow", { ox: 520, oy: 520, w: 1040, h: 560 }, g({ fill: "none", "stroke-linecap": "round", filter: "url(#cs-rough)" }, ...["#d98a72", "#e2b27a", "#e6d38e", "#9fbf8f", "#93b3cf", "#a79bc6"].map((c, i) => h("path", { class: "cs-rainbow-band", d: `M${-440 + i * 14} 0 A${440 - i * 14} ${420 - i * 14} 0 0 1 ${440 - i * 14} 0`, stroke: c, "stroke-width": 13, opacity: 0.55 }))))
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
  for (const [x, y] of tarpBulbs()) bulbs += glow(SPOTS.tarpX + x, SPOTS.tarpY + y, 22, "cs-bulb-glow");
  const r = rng(12);
  let flies = "";
  for (let i = 0; i < 14; i += 1) flies += h("circle", { class: "cs-firefly", cx: 3800 + r() * 1300, cy: 980 + r() * 320, r: 8, fill: "url(#cs-firefly)" });
  return (
    `<div class="cs-layer cs-lights"><svg viewBox="0 0 ${PANEL_COUNT * PANEL_W} ${STAGE_H}" width="${PANEL_COUNT * PANEL_W}" height="${STAGE_H}">` +
    g({ class: "cs-lights-evening" }, g({ class: "cs-lights-tarp" }, bulbs), glow(SPOTS.heaterX, SPOTS.tarpY - 40, 70, "cs-heater-glow"), glow(SPOTS.eveningFireX, SPOTS.tarpY + 80, 170, "cs-fire-glow")) +
    g({ class: "cs-lights-tent" }, glow(SPOTS.tentX + 10, 1010, 230, "cs-tentlamp-glow")) +
    g({ class: "cs-fireflies" }, flies) +
    `</svg></div>`
  );
}

function rainLayer(): string {
  // 遠景雨：細、淡、短；近景雨：粗、長、少。每條長短角度略不同，兩份疊起來往下捲接縫看不出來
  const sheet = (seed: number, count: number, len: number, width: number, color: string, opacity: number) => {
    const r = rng(seed);
    let d = "";
    for (let i = 0; i < count; i += 1) {
      const x = r() * 1100 - 50;
      const y = r() * 1400;
      const l = len * (0.6 + r() * 0.8);
      d += `M${x.toFixed(0)} ${y.toFixed(0)} l${(-l * 0.28).toFixed(1)} ${l.toFixed(1)} `;
    }
    const one = h("path", { d, stroke: color, "stroke-width": width, "stroke-linecap": "round", fill: "none", opacity });
    return one + g({ transform: `translate(0 ${STAGE_H})` }, one);
  };
  return (
    `<div class="cs-layer cs-rain">` +
    `<svg class="cs-rain-far" viewBox="0 0 ${PANEL_W} ${STAGE_H * 2}" width="${PANEL_W}" height="${STAGE_H * 2}">${sheet(33, 150, 20, 1.1, "#8aa0b4", 0.45)}</svg>` +
    `<svg class="cs-rain-near" viewBox="0 0 ${PANEL_W} ${STAGE_H * 2}" width="${PANEL_W}" height="${STAGE_H * 2}">${sheet(34, 45, 46, 2.2, "#6f8aa4", 0.6)}</svg>` +
    `</div>`
  );
}

/** 開車時最前景的草叢與柵欄柱：比地面移動得快，做出速度感（車本身保持穩定）。 */
function foregroundLayer(): string {
  const r = rng(71);
  let tufts = "";
  for (let x = 40; x < 4600; x += 180 + r() * 260) {
    let blades = "";
    for (let k = 0; k < 9; k += 1) {
      const bx = x + (r() - 0.5) * 40;
      const hgt = 40 + r() * 50;
      const lean = (r() - 0.5) * 30;
      blades += `M${(bx - 3).toFixed(0)} 1400 Q${(bx + lean * 0.4).toFixed(0)} ${(1400 - hgt * 0.6).toFixed(0)} ${(bx + lean).toFixed(0)} ${(1400 - hgt).toFixed(0)} Q${(bx + lean * 0.3 + 4).toFixed(0)} ${(1400 - hgt * 0.5).toFixed(0)} ${(bx + 4).toFixed(0)} 1400 Z `;
    }
    tufts += h("path", { d: blades, fill: r() > 0.5 ? "#4f6d32" : "#5d7d39" });
  }
  return `<div class="cs-layer cs-fg"><svg viewBox="0 0 4600 ${STAGE_H}" width="4600" height="${STAGE_H}">${tufts}</svg></div>`;
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
    foregroundLayer() +
    `<div class="cs-layer cs-tint"></div>` +
    nightSky() +
    `<div class="cs-layer cs-lights-wrap">${lightsTrack()}</div>` +
    rainLayer() +
    `</div>` +
    `<div class="cs-layer cs-pov"><svg viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}">${povDesk(rng(515))}</svg></div>` +
    thanksLayer() +
    `<div class="cs-flash"></div>` +
    `<svg class="cs-paper" viewBox="0 0 ${PANEL_W} ${STAGE_H}" width="${PANEL_W}" height="${STAGE_H}" preserveAspectRatio="none">${h("rect", { width: PANEL_W, height: STAGE_H, fill: "#fff", "fill-opacity": 0.01, filter: "url(#cs-paper)" })}</svg>` +
    `</div>`
  );
}
