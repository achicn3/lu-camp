// 會動的角色與道具：人物（各種姿勢）、露營車、帳篷。每個都有 class 讓時間軸抓得到要動的關節。
// 座標：人物原點在腳底中央、面向右；露營車原點在前後輪中間的地面。
import { INK, INK_SOFT, doodleText, fillPath, g, h, hatchArea, inkPath, shadow, shape } from "./svg";

const SKIN = "#efc9a6";
const SKIN_SHADE = "#e6b692";
const JACKET = "#c2643f";
const JACKET_SHADE = "#a8502f";
const SCARF = "#e2b24a";
const PANTS = "#4c5d73";
const PANTS_SHADE = "#3c4a5c";
const BOOT = "#5a3b24";
const BEANIE = "#d9a441";
const BEANIE_BAND = "#b8862f";

/** 頭（含毛帽、耳朵、閉眼微笑、腮紅），原點在臉中心。 */
function head(r = 25, closedEyes = true): string {
  const face = h("circle", { r, fill: SKIN, filter: "url(#cs-pencil)" });
  const ear = h("ellipse", { cx: -r * 0.88, cy: 4, rx: 5, ry: 7, fill: SKIN_SHADE });
  const hat =
    fillPath(`M${-r - 1} -6 C${-r - 3} ${-r - 16} ${r + 3} ${-r - 20} ${r + 3} -8 Z`, BEANIE) +
    fillPath(`M${-r - 3} -8 L${r + 5} -10 L${r + 5} 2 L${-r - 3} 4 Z`, BEANIE_BAND) +
    h("circle", { cx: 0, cy: -r - 17, r: 9, fill: "#ecc873", filter: "url(#cs-pencil)" });
  let ribs = "";
  for (let x = -r; x < r + 4; x += 3.5) ribs += `M${x.toFixed(1)} -9 L${(x + 0.4).toFixed(1)} 2 `;
  // 帽身針織：一排排小 V 字
  let knit = "";
  for (let row = 0; row < 3; row += 1) {
    const y = -13 - row * 7;
    const half = r * (1 - row * 0.18);
    for (let x = -half + 3; x < half; x += 6) knit += `M${x.toFixed(1)} ${y} l2 3 l2 -3 `;
  }
  // 毛球：一圈短短的毛
  let fluff = "";
  for (let i = 0; i < 16; i += 1) {
    const a = (i / 16) * Math.PI * 2;
    fluff += `M${(Math.cos(a) * 7).toFixed(1)} ${(-r - 17 + Math.sin(a) * 7).toFixed(1)} l${(Math.cos(a) * 3.5).toFixed(1)} ${(Math.sin(a) * 3.5).toFixed(1)} `;
  }
  const eyes = closedEyes ? `M8 4 q5 5 10 0 M-10 2 q4 4 8 0` : `M10 2 m-2 0 a2 2 0 1 0 4 0 a2 2 0 1 0 -4 0 M-6 1 m-2 0 a2 2 0 1 0 4 0 a2 2 0 1 0 -4 0`;
  const lines = g(
    { filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-width": 2.4, "stroke-linecap": "round", "stroke-linejoin": "round" },
    h("circle", { r }),
    h("ellipse", { cx: -r * 0.88, cy: 4, rx: 5, ry: 7, "stroke-width": 1.8 }),
    h("path", { d: `M${-r - 1} -6 C${-r - 3} ${-r - 16} ${r + 3} ${-r - 20} ${r + 3} -8` }),
    h("path", { d: `M${-r - 3} -8 L${r + 5} -10 L${r + 5} 2 L${-r - 3} 4 Z`, "stroke-width": 2 }),
    h("circle", { cx: 0, cy: -r - 17, r: 9, "stroke-width": 1.8 }),
    h("path", { d: ribs, "stroke-width": 1, opacity: 0.45, stroke: INK_SOFT }),
    h("path", { d: knit, "stroke-width": 0.9, opacity: 0.5, stroke: "#8a5f1c" }),
    h("path", { d: fluff, "stroke-width": 1.1, opacity: 0.8, stroke: "#9c7a2c" }),
    h("path", { d: `M3 ${-r - 8} C5 ${-r + 2} 7 -18 7 -10`, "stroke-width": 1, opacity: 0.55, stroke: INK_SOFT, "stroke-dasharray": "2 2" }),
    h("path", { d: `M${-r - 3} -8 C${-r * 0.3} -12 ${r * 0.4} -13 ${r + 5} -10`, "stroke-width": 0.9, opacity: 0.5, stroke: "#8a5f1c" }),
    h("path", { d: eyes, "stroke-width": 1.8 }),
    h("path", { d: "M18 14 q4 3 8 0", "stroke-width": 1.6 }),
  );
  const hair = h("path", { d: `M${-r + 2} 2 q4 6 2 12 q5 -3 7 -10`, fill: "#6b4a33" });
  const faceShade = h("path", { d: `M${-r * 0.2} ${r * 0.95} C${r * 0.5} ${r * 0.9} ${r * 0.9} ${r * 0.4} ${r * 0.98} 0 C${r} ${r * 0.6} ${r * 0.5} ${r} ${-r * 0.2} ${r * 0.95} Z`, fill: SKIN_SHADE, opacity: 0.55 });
  return face + faceShade + ear + hair + h("circle", { cx: 14, cy: 10, r: 4, fill: "#e89b86", opacity: 0.7 }) + hat + lines;
}

/** 一隻手臂（袖子＋手），原點在肩膀，自然垂下長 len。 */
function arm(len: number, cls: string, extra = ""): string {
  const d = `M-9 -4 C-10 ${len * 0.4} -8 ${len * 0.8} -6 ${len} L8 ${len} C9 ${len * 0.7} 10 ${len * 0.35} 9 -4 Z`;
  return g(
    { class: cls },
    fillPath(d, JACKET),
    h("path", { d: `M-9 ${len - 10} L9 ${len - 10} L8 ${len} L-6 ${len} Z`, fill: JACKET_SHADE }),
    h("circle", { cx: 1, cy: len + 6, r: 8, fill: SKIN, filter: "url(#cs-pencil)" }),
    extra,
    g({ filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-width": 2.2, "stroke-linecap": "round", "stroke-linejoin": "round" }, h("path", { d }), h("circle", { cx: 1, cy: len + 6, r: 8, "stroke-width": 1.8 })),
  );
}

/** 一條腿（褲管＋靴子），原點在髖，長 len。 */
function leg(len: number, cls: string, shade = false): string {
  const d = `M-11 0 L11 0 L10 ${len - 8} L-9 ${len - 8} Z`;
  const boot = `M-10 ${len - 12} L12 ${len - 12} C22 ${len - 10} 24 ${len} 18 ${len + 2} L-10 ${len + 2} Z`;
  return g(
    { class: cls },
    fillPath(d, shade ? PANTS_SHADE : PANTS),
    fillPath(boot, BOOT),
    g({ filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-width": 2.2, "stroke-linejoin": "round" }, h("path", { d }), h("path", { d: boot })),
  );
}

/** 軀幹（外套、拉鍊、口袋、圍巾、小背包），原點在髖。 */
function torso(withPack = true): string {
  const body = "M-26 4 C-31 -30 -26 -60 -10 -72 C6 -80 24 -72 28 -52 C32 -32 30 -10 26 4 Z";
  const scarf = "M-14 -70 C0 -62 16 -62 26 -70 L28 -58 C14 -50 -2 -52 -16 -60 Z M4 -56 L0 -30 L10 -34 Z";
  const pack = "M-46 -60 C-50 -40 -48 -16 -40 -6 L-24 -6 L-24 -64 C-32 -68 -42 -66 -46 -60 Z";
  return (
    (withPack ? shape(pack, "#5f7f58", 2.2, "cs-hatch-fine") : "") +
    fillPath(body, JACKET) +
    h("path", { d: "M-26 4 C-31 -30 -26 -60 -10 -72 L-2 -70 C-16 -48 -16 -20 -12 4 Z", fill: "url(#cs-hatch)", filter: "url(#cs-rough)" }) +
    fillPath("M-2 -16 L20 -16 L20 -2 L-2 -2 Z", JACKET_SHADE) +
    fillPath(scarf, SCARF) +
    g(
      { filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-width": 2.4, "stroke-linecap": "round", "stroke-linejoin": "round" },
      h("path", { d: body }),
      h("path", { d: "M8 -56 L10 2", "stroke-width": 1.6 }),
      h("path", { d: "M-2 -16 L20 -16 L20 -2 L-2 -2 Z", "stroke-width": 1.6 }),
      h("path", { d: scarf, "stroke-width": 2 }),
    ) +
    // 和坐著那一位同一件外套：下襬收邊、拉鍊齒、口袋釦、肩線、圍巾針織紋
    fillPath("M-26 -4 C-8 0 12 0 26 -4 L26 4 L-26 4 Z", JACKET_SHADE) +
    inkPath("M-26 -4 C-8 0 12 0 26 -4", 1.1) +
    inkPath("M7 -50 l3 0 M7 -42 l3 0 M8 -34 l3 0 M8 -26 l3 0 M9 -18 l3 0 M9 -10 l3 0", 0.8) +
    inkPath("M-2 -10 L20 -10", 0.9) +
    h("circle", { cx: 9, cy: -7, r: 1.4, fill: "#3a2a20" }) +
    inkPath("M-18 -64 C-20 -48 -22 -34 -24 -22 M22 -62 C26 -50 28 -40 28 -30", 1) +
    inkPath("M-8 -66 l1 6 M2 -64 l0 6 M12 -64 l-1 6 M20 -66 l-1 5", 0.8) +
    inkPath("M2 -40 q5 3 10 0 M-14 -30 q4 3 8 0", 0.8, { opacity: 0.7 })
  );
}

/** 站姿／走路的人：legs、arms 可擺動；hold 為前手拿的東西。 */
export function standingPerson(cls: string, hold = "", carry = ""): string {
  return g(
    { class: `cs-person ${cls}` },
    shadow(0, 2, 40, 7),
    g({ class: "cs-bob" },
      g({ transform: "translate(-4 -78)" }, leg(78, "cs-leg-back", true)),
      g({ transform: "translate(0 -150)" }, arm(58, "cs-arm-back")),
      g({ transform: "translate(4 -78)" }, leg(78, "cs-leg-front")),
      g({ transform: "translate(0 -76)" }, torso()),
      g({ transform: "translate(8 -176)", class: "cs-head" }, head()),
      // 抱在胸前的東西（紙箱、保冷箱），畫在身體與前手之間
      g({ class: "cs-carry", transform: "translate(22 -104)" }, carry),
      g({ transform: "translate(6 -146)" }, arm(58, "cs-arm-front", hold)),
    ),
  );
}

/** 抱在胸前搬的紙箱、保冷箱（原點在抱的位置）。 */
export const CARRY_BOX = g(
  { class: "cs-carry-box", opacity: 0 },
  shape("M-30 -26 L30 -26 L30 22 L-30 22 Z", "#c99a63", 2),
  inkPath("M-30 -10 L30 -10 M0 -26 L0 -10", 1.2),
  hatchArea("M12 -26 L30 -26 L30 22 L12 22 Z", "cs-hatch-fine", 0.8),
);
export const CARRY_COOLER = g(
  { class: "cs-carry-cooler", opacity: 0 },
  shape("M-34 -18 L34 -18 L32 26 L-32 26 Z", "#5fa38a", 2, "cs-hatch-fine"),
  shape("M-37 -28 L37 -28 L37 -17 L-37 -17 Z", "#f3e9d2", 1.8),
  inkPath("M-10 -34 L10 -34 M-10 -34 L-12 -28 M10 -34 L12 -28", 1.6),
);

/** 手上的收納袋（帳篷），接在手臂末端。 */
export const TENT_BAG = g(
  { class: "cs-tent-bag" },
  shape("M-22 66 C-24 58 26 58 24 66 L24 104 C26 112 -24 112 -22 104 Z", "#b97f2f", 2.2, "cs-hatch-fine"),
  inkPath("M-6 64 C-6 54 8 54 8 64", 2),
);

/** 手上的馬克杯（冒煙）。 */
export const MUG_IN_HAND = g(
  { class: "cs-mug" },
  shape("M-4 58 L22 58 L20 80 L-2 80 Z", "#f4efe4", 2),
  inkPath("M22 63 c9 0 9 12 -1 12", 1.8),
  inkPath("M6 52 c-6 -10 6 -14 0 -24 M14 52 c-6 -8 6 -12 0 -20", 1.6, { class: "cs-steam", opacity: 0.6 }),
);

/** 蹲著敲營釘的人（面向右）：cs-hammer-arm 繞肩膀擺動。 */
export function crouchingPerson(): string {
  const thigh = "M-6 -52 L34 -58 L40 -40 L0 -30 Z";
  const shin = "M26 -48 L44 -48 L42 -8 L26 -8 Z";
  const kneel = "M-10 -44 L6 -40 L-2 -4 L-40 -2 L-40 -14 L-16 -16 Z";
  const mallet =
    fillPath("M-3 44 L3 44 L4 104 L-4 104 Z", "#a07044") +
    shape("M-18 100 L18 100 L18 124 L-18 124 Z", "#6b4a33", 2.2) +
    inkPath("M-3 44 L-4 104 M3 44 L4 104", 1.8);
  return g(
    { class: "cs-person cs-pose-hammer" },
    shadow(4, 2, 50, 7),
    shape(kneel, PANTS_SHADE, 2.2),
    shape("M-44 -16 L-40 -16 L-40 2 L-58 2 C-62 -6 -56 -14 -44 -16 Z", BOOT, 2),
    shape(thigh, PANTS, 2.2),
    shape(shin, PANTS, 2.2),
    shape("M24 -12 L46 -12 C56 -10 58 0 52 2 L24 2 Z", BOOT, 2),
    // 上半身一起前傾（繞髖關節），肩膀帶動手臂敲下去
    g(
      { transform: "translate(-2 -40)" },
      g(
        { class: "cs-hammer-body" },
        g({ transform: "translate(2 40)" },
          g({ transform: "translate(-2 -40) rotate(28)" }, torso()),
          g({ transform: "translate(44 -142) rotate(12)", class: "cs-head" }, head()),
          g({ transform: "translate(30 -104)" }, g({ class: "cs-hammer-arm" }, arm(44, "cs-arm-hammer", mallet))),
        ),
      ),
    ),
  );
}

/** 從背後看、坐在石頭上看雲海的人。 */
export function backViewPerson(): string {
  const back = "M-34 -20 C-38 -60 -30 -92 0 -98 C30 -92 38 -60 34 -20 Z";
  return g(
    { class: "cs-person cs-pose-cliff" },
    shape("M-70 0 C-74 -30 -40 -40 0 -38 C40 -40 74 -30 70 0 Z", "#8d8d84", 2.4, "cs-hatch-fine"),
    shape("M-26 -96 C-30 -70 -28 -48 -20 -40 L20 -40 C28 -48 30 -70 26 -96 Z", "#5f7f58", 2.2, "cs-hatch-fine"),
    fillPath(back, JACKET),
    inkPath(back, 2.4),
    shape("M-24 -100 C-10 -92 10 -92 24 -100 L26 -88 C10 -80 -10 -80 -26 -88 Z", SCARF, 2),
    g({ class: "cs-head", transform: "translate(0 -120)" },
      h("circle", { r: 26, fill: "#6b4a33", filter: "url(#cs-pencil)" }),
      fillPath("M-27 -4 C-29 -40 29 -40 27 -4 Z", BEANIE),
      fillPath("M-28 -6 L28 -6 L28 6 L-28 6 Z", BEANIE_BAND),
      h("circle", { cy: -36, r: 9, fill: "#ecc873", filter: "url(#cs-pencil)" }),
      h("ellipse", { cx: -27, cy: 10, rx: 5, ry: 7, fill: SKIN_SHADE }),
      h("ellipse", { cx: 27, cy: 10, rx: 5, ry: 7, fill: SKIN_SHADE }),
      inkPath("M-26 8 C-24 26 24 26 26 8 M-27 -4 C-29 -40 29 -40 27 -4 M-28 -6 L28 -6 L28 6 L-28 6 Z", 2.2),
      h("circle", { cy: -36, r: 9, fill: "none", stroke: INK, "stroke-width": 1.8, filter: "url(#cs-rough)" }),
    ),
    g({ transform: "translate(30 -86)" }, g({ class: "cs-point-arm", transform: "rotate(-20)" }, arm(52, "cs-arm-point"))),
  );
}

/** 坐在木頭上烤棉花糖的人（面向右），cs-stick-arm 可微調，cs-marsh 是棉花糖。 */
export function roastingPerson(): string {
  const thigh = "M-10 -48 L40 -52 L42 -34 L-6 -28 Z";
  const shin = "M28 -46 L46 -46 L44 -8 L28 -8 Z";
  const stick = inkPath("M0 50 L92 30", 3, { stroke: "#6b4a33" });
  const marsh = g(
    { class: "cs-marsh", transform: "translate(92 30)" },
    h("g", { class: "cs-marsh-fire", opacity: 0 }, fillPath("M-8 -6 C-12 -26 0 -30 2 -44 C6 -30 16 -24 10 -6 Z", "#f08a2c") + fillPath("M-3 -6 C-5 -18 1 -22 2 -30 C4 -20 8 -16 5 -6 Z", "#f7c64a")),
    h("rect", { class: "cs-marsh-body", x: -10, y: -8, width: 22, height: 18, rx: 6, fill: "#fbf6ee", stroke: INK, "stroke-width": 2, filter: "url(#cs-rough)" }),
  );
  return g(
    { class: "cs-person cs-pose-roast" },
    shape("M-60 -4 L60 -4 L62 -30 L-58 -30 Z", "#7a4d29", 2.4, "cs-hatch-fine"),
    shape("M60 -30 m-6 0 a8 13 0 1 0 12 0 a8 13 0 1 0 -12 0", "#c99a63", 1.8),
    shape(thigh, PANTS, 2.2),
    shape(shin, PANTS, 2.2),
    shape("M26 -12 L48 -12 C58 -10 60 0 54 2 L26 2 Z", BOOT, 2),
    g({ transform: "translate(0 -40)" }, torso()),
    g({ transform: "translate(12 -140)", class: "cs-head" }, head()),
    g({ transform: "translate(8 -106)" }, g({ class: "cs-stick-arm", transform: "rotate(-62)" }, arm(46, "cs-arm-stick", g({ transform: "rotate(62 1 52)" }, stick, marsh)))),
  );
}

/** 復古露營車（側面，面向右）。原點在兩輪中間的地面；cs-wheel 會轉、cs-van-door 會開。 */
export function camperVan(): string {
  const body = "M-190 -40 L-190 -170 C-190 -196 -170 -206 -140 -206 L80 -206 C112 -206 132 -190 150 -160 L186 -104 C196 -90 198 -74 198 -60 L198 -40 Z";
  const lower = "M-190 -40 L-190 -104 L196 -104 C198 -90 198 -74 198 -60 L198 -40 Z";
  const wheel = (cx: number) =>
    g(
      { transform: `translate(${cx} -36)` },
      g(
        { class: "cs-wheel" },
        h("circle", { r: 34, fill: "#3a3330", filter: "url(#cs-pencil)" }),
        // 輪胎胎紋（跟著輪子轉）
        h("circle", { r: 29, fill: "none", stroke: "#57504b", "stroke-width": 4, "stroke-dasharray": "5 4" }),
        h("circle", { r: 18, fill: "#d9dbd6", stroke: INK, "stroke-width": 2 }),
        h("circle", { r: 11, fill: "#b8bcbf", stroke: INK, "stroke-width": 1.2 }),
        inkPath("M-11 0 L11 0 M0 -11 L0 11", 1.2),
        h("circle", { r: 3, fill: "#6e7276" }),
        h("circle", { r: 34, fill: "none", stroke: INK, "stroke-width": 2.6, filter: "url(#cs-rough)" }),
      ),
    );
  const word = doodleText("露坑", { font: "marker", size: 74, fill: "#fff1d6", outline: "#3a2210", outlineWidth: 9, drop: 5 });
  return g(
    { class: "cs-van" },
    shadow(4, -2, 220, 12, 0.22),
    g(
      { class: "cs-van-body" },
      // 車頂架：捲起的睡墊、折疊椅
      shape("M-150 -214 L100 -214 L100 -222 L-150 -222 Z", "#6b5a44", 2),
      inkPath("M-140 -206 L-140 -222 M-40 -206 L-40 -222 M60 -206 L60 -222", 2),
      shape("M-130 -222 C-140 -222 -140 -250 -130 -250 L-30 -250 C-20 -250 -20 -222 -30 -222 Z", "#3f6b52", 2.2, "cs-hatch-fine"),
      shape("M0 -222 L40 -262 L50 -256 L14 -222 Z M20 -222 L70 -250 L76 -242 L34 -222 Z", "#c9553a", 2),
      fillPath(body, "#f3e9d2"),
      fillPath(lower, "#5fa38a"),
      h("path", { d: lower, fill: "url(#cs-hatch-fine)", filter: "url(#cs-rough)" }),
      fillPath("M-190 -110 L196 -110 L196 -98 L-190 -98 Z", "#e2b24a"),
      // 窗戶：前窗有司機
      shape("M100 -190 L124 -190 C136 -176 148 -154 158 -132 L100 -132 Z", "#bfe0ea", 2.4),
      g({ class: "cs-driver", transform: "translate(120 -144)" }, h("circle", { cy: -18, r: 17, fill: SKIN, stroke: INK, "stroke-width": 2, filter: "url(#cs-rough)" }), fillPath("M-18 -22 C-20 -48 20 -48 20 -24 Z", BEANIE), inkPath("M-18 -22 C-20 -48 20 -48 20 -24", 2), inkPath("M4 -16 q4 4 8 0", 1.6)),
      h("path", { d: "M106 -184 L120 -184 L110 -154 Z", fill: "#fff", opacity: 0.7 }),
      shape("M-170 -186 L-60 -186 L-60 -128 L-170 -128 Z", "#bfe0ea", 2.4),
      h("path", { d: "M-160 -180 L-138 -180 L-160 -150 Z", fill: "#fff", opacity: 0.7 }),
      inkPath("M-170 -170 C-150 -176 -80 -176 -60 -170", 1.6, { opacity: 0.7 }),
      // 側門（會打開）
      g({ class: "cs-van-door-hole" }, h("path", { d: "M-40 -190 L90 -190 L90 -46 L-40 -46 Z", fill: "#3a2a20", filter: "url(#cs-pencil)" })),
      g(
        { class: "cs-van-door" },
        fillPath("M-40 -190 L90 -190 L90 -46 L-40 -46 Z", "#f3e9d2"),
        fillPath("M-40 -104 L90 -104 L90 -46 L-40 -46 Z", "#5fa38a"),
        fillPath("M-40 -110 L90 -110 L90 -98 L-40 -98 Z", "#e2b24a"),
        shape("M-26 -176 L76 -176 L76 -128 L-26 -128 Z", "#bfe0ea", 2.2),
        inkPath("M-40 -190 L90 -190 L90 -46 L-40 -46 Z", 2.4),
        inkPath("M70 -84 L84 -84", 3.2),
      ),
      g({ transform: "translate(-120 -68) rotate(-4)" }, word),
      // 鈑件分界、窗框、門把、後照鏡、車身下緣陰影
      inkPath("M-100 -206 L-100 -40 M92 -196 L96 -40", 1.1, { opacity: 0.8 }),
      inkPath("M-176 -190 L-54 -190 L-54 -124 L-176 -124 Z M96 -194 L130 -194", 1.1),
      hatchArea("M-190 -56 L198 -56 L198 -40 L-190 -40 Z", "cs-hatch-fine", 0.9),
      inkPath("M-180 -66 L-170 -66 M100 -96 L112 -96", 2),
      shape("M160 -142 L176 -150 L180 -136 L166 -132 Z", "#9aa0a6", 1.4),
      inkPath(body, 3),
      inkPath("M-190 -104 L196 -104", 2),
      // 車燈、保險桿
      shape("M184 -98 C194 -98 199 -86 199 -76 L184 -76 Z", "#f7e3a0", 2),
      h("path", { d: "M188 -94 L192 -84", stroke: "#fff", "stroke-width": 2, "stroke-linecap": "round", opacity: 0.8 }),
      shape("M-196 -52 L-184 -52 L-184 -36 L-196 -36 Z M190 -52 L206 -52 L206 -36 L190 -36 Z", "#9aa0a6", 2),
    ),
    wheel(-110),
    wheel(120),
    g({ class: "cs-exhaust", transform: "translate(-200 -40)" }, h("circle", { cx: -14, cy: -6, r: 10, fill: "#e8e2d6", stroke: INK, "stroke-width": 1.6, opacity: 0 }), h("circle", { cx: -30, cy: -14, r: 13, fill: "#e8e2d6", stroke: INK, "stroke-width": 1.6, opacity: 0 })),
  );
}

/** 帳篷：cs-tent-body 由收合（scaleY 0）撐開；原點在帳篷底中央。 */
export function tent(): string {
  // 品牌字不要比人物搶眼：暖米白、外框與陰影都放淡一點
  const word = doodleText("露坑", { font: "marker", size: 80, fill: "#ecdcbd", outline: "#76502c", outlineWidth: 9, drop: 3 });
  return g(
    { class: "cs-tent" },
    g({ class: "cs-tent-shadow" }, shadow(10, 8, 210, 18, 0.16), h("path", { d: "M-176 1 C-60 8 80 8 246 2 L244 7 C80 14 -60 14 -174 7 Z", fill: INK, opacity: 0.35 })),
    g(
      { class: "cs-tent-body" },
      fillPath("M-170 0 L0 -320 L170 0 Z", "#dca64a"),
      fillPath("M0 -320 L170 0 L240 0 L58 -320 Z", "#b97f2f"),
      fillPath("M-54 0 L0 -160 L54 0 Z", "#4a3322"),
      fillPath("M-24 0 L0 -110 L24 0 Z", "#2f2016"),
      fillPath("M-60 0 C-46 -60 -28 -120 0 -160 C-12 -116 -20 -60 -24 0 Z", "#e9bd67"),
      h("path", { d: "M0 -320 L170 0 L240 0 L58 -320 Z", fill: "url(#cs-hatch)", filter: "url(#cs-rough)" }),
      h("path", { d: "M-54 0 L0 -160 L54 0 Z", fill: "url(#cs-cross)", filter: "url(#cs-rough)", opacity: 0.7 }),
      // 帳布：順著布面方向的纖維細線、下襬收邊、從頂端拉出來的皺褶、門邊拉鍊、門簾綁帶
      h("path", { d: "M-170 0 L0 -320 L-54 0 Z M54 0 L0 -320 L170 0 Z", fill: "url(#cs-fabric)", opacity: 0.9 }),
      h("path", { d: "M0 -320 L170 0 L240 0 L58 -320 Z", fill: "url(#cs-fabric-side)", opacity: 0.9 }),
      fillPath("M-170 0 L-164 -14 L-54 -14 L-54 0 Z M54 0 L54 -14 L164 -14 L170 0 Z", "#b9822f"),
      fillPath("M170 0 L166 -12 L236 -12 L240 0 Z", "#8f6224"),
      inkPath("M-6 -300 C-26 -240 -52 -170 -88 -104 M6 -296 C22 -236 44 -176 76 -110 M-14 -286 C-44 -236 -84 -190 -120 -150 M-150 -16 C-134 -34 -116 -42 -96 -46 M150 -16 C134 -34 118 -40 100 -44", 1, { opacity: 0.7 }),
      inkPath("M60 -300 C90 -230 130 -150 178 -70 M80 -250 C120 -190 160 -120 206 -40", 1, { opacity: 0.55 }),
      inkPath("M-50 -8 l-4 -2 M-46 -20 l-4 -2 M-42 -32 l-4 -2 M-38 -44 l-4 -2 M-34 -56 l-4 -2 M-30 -68 l-4 -2 M-26 -80 l-4 -2 M-22 -92 l-4 -2 M-18 -104 l-4 -2 M-14 -116 l-4 -2 M-10 -128 l-4 -2 M50 -8 l4 -2 M46 -20 l4 -2 M42 -32 l4 -2 M38 -44 l4 -2 M34 -56 l4 -2 M30 -68 l4 -2 M26 -80 l4 -2 M22 -92 l4 -2 M18 -104 l4 -2 M14 -116 l4 -2 M10 -128 l4 -2", 0.9),
      shape("M-62 -44 L-40 -48 L-39 -40 L-61 -36 Z M-50 -100 L-30 -104 L-29 -96 L-49 -92 Z", "#8a5a24", 1.1),
      // 帳門的布厚度（門邊一條較深的布緣）
      fillPath("M0 -160 L54 0 L45 0 L-2 -148 Z", "#9a6a28"),
      inkPath("M45 0 L-2 -148", 0.9),
      g({ transform: "translate(2 -170) rotate(-7)" }, word, g({ transform: "translate(74 -58)" }, h("path", { d: "M0 -12 l3.5 10 l10 1 l-8 6 l2.5 10 l-8 -6 l-8 6 l2.5 -10 l-8 -6 l10 -1z", fill: "#e8674a", stroke: "#3a2210", "stroke-width": 2.2, filter: "url(#cs-rough)" }))),
      // 夜晚：人進了帳篷、裡面點燈——布透出暖光，帳壁上一個坐著的人影
      g(
        { class: "cs-tent-lit", opacity: 0 },
        h("path", { d: "M-170 0 L0 -320 L170 0 Z", fill: "#ffcf7a", opacity: 0.55 }),
        h("path", { d: "M0 -320 L170 0 L240 0 L58 -320 Z", fill: "#f0a850", opacity: 0.45 }),
        h("path", { d: "M-54 0 L0 -160 L54 0 Z", fill: "#ffe7a6" }),
        h("path", { d: "M-60 0 C-46 -60 -28 -120 0 -160 C-12 -116 -20 -60 -24 0 Z", fill: "#f7c46a" }),
        h("path", { d: "M70 -8 C66 -40 72 -70 86 -84 C80 -96 84 -112 98 -112 C112 -112 116 -96 108 -84 C124 -70 128 -40 124 -8 Z", fill: "#7a4a24", opacity: 0.32 }),
      ),
      g(
        { filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-linecap": "round", "stroke-linejoin": "round" },
        h("path", { d: "M-170 0 L0 -320 L170 0", "stroke-width": 3.4 }),
        h("path", { d: "M0 -320 L58 -320 L240 0 L170 0", "stroke-width": 3 }),
        h("path", { d: "M-54 0 L0 -160 L54 0", "stroke-width": 2.6 }),
        h("path", { d: "M-60 0 C-46 -60 -28 -120 0 -160", "stroke-width": 2.2 }),
        h("path", { d: "M-85 -160 L0 -304 M85 -160 L10 -304", "stroke-width": 1.6, "stroke-dasharray": "6 6", opacity: 0.7 }),
        h("path", { d: "M-150 -14 L150 -14", "stroke-width": 1.4, "stroke-dasharray": "5 6", opacity: 0.6 }),
        h("path", { d: "M0 -320 L0 -368", "stroke-width": 3.2 }),
        h("path", { d: "M-170 0 L-226 30 M240 0 L262 22 M58 -320 L250 12", "stroke-width": 1.2, stroke: INK_SOFT }),
        h("path", { d: "M-200 16 l6 -3 l3 5 l-6 3 Z M248 6 l6 -2 l3 5 l-6 2 Z M196 -80 l6 -1 l1 6 l-6 1 Z", "stroke-width": 1 }),
        h("path", { d: "M-5 -372 L5 -372 L4 -366 L-4 -366 Z", "stroke-width": 1.4 }),
      ),
      g({ class: "cs-flag", transform: "translate(0 -364)" }, fillPath("M0 0 L42 10 L0 24 Z", "#c9553a"), inkPath("M0 0 L42 10 L0 24", 1.8)),
    ),
    g({ class: "cs-pegs", filter: "url(#cs-rough)", stroke: INK, "stroke-width": 2.6, "stroke-linecap": "round", fill: "none" }, h("path", { class: "cs-peg", d: "M-230 22 l5 16 M-231 22 q-4 -4 1 -6" }), h("path", { class: "cs-peg", d: "M260 16 l5 16 M259 16 q-4 -4 1 -6" }), h("path", { class: "cs-peg", d: "M248 6 l5 16 M247 6 q-4 -4 1 -6" })),
  );
}

/** 繞 (px,py) 轉的關節：外層平移到支點，class 那層只放 rotate()，內層再平移回來。 */
function joint(cls: string, px: number, py: number, rotate: number, body: string): string {
  return g({ transform: `translate(${px} ${py})` }, g({ class: cls, transform: `rotate(${rotate})` }, g({ transform: `translate(${-px} ${-py})` }, body)));
}

/** 喝咖啡的人手上杯子的三個位置：放在胸前、湊到嘴邊喝、舉高乾杯。 */
export const SIT_POSES = {
  // 袖子從右肩出發、手肘往外下方，手腕在杯把右下方；三個位置的手腕都剛好等於杯子的位移
  rest: { cup: "translate(0 0)", sleeve: "M588 976 Q642 992 626 1030" },
  sip: { cup: "translate(-12 -50)", sleeve: "M588 976 Q654 1012 614 980" },
  cheers: { cup: "translate(22 -86)", sleeve: "M588 976 Q646 1006 648 944" },
} as const;

/**
 * 坐在露營椅上喝咖啡的人（店主定稿樣張那一位）。沿用樣張的座標，原點移到椅腳中間 (560,1150)。
 * cs-cup-arm 舉杯喝／乾杯，cs-nod-head 點頭，cs-steam 冒煙。
 */
export function sittingPerson(): string {
  const INKG = { filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-width": 2.6, "stroke-linecap": "round", "stroke-linejoin": "round" };
  const legs =
    // 坐下的重量：椅面被壓得往下彎、大腿底下一道影子；靴子踩在棧板上的接地影子
    h("path", { d: "M498 1064 C530 1080 580 1082 608 1066 L606 1074 C580 1090 530 1088 500 1072 Z", fill: INK, opacity: 0.22 }) +
    h("ellipse", { cx: 660, cy: 1113, rx: 26, ry: 3.2, fill: INK, opacity: 0.3 }) +
    fillPath("M548 1054 C580 1048 612 1048 640 1052 L652 1102 L632 1106 L622 1072 C598 1070 572 1074 552 1078 Z", PANTS_SHADE) +
    fillPath("M540 1060 C574 1052 612 1054 646 1060 L662 1108 L640 1112 L628 1080 C602 1078 572 1082 546 1086 Z", PANTS) +
    fillPath("M636 1108 L678 1108 L678 1096 L662 1092 L640 1096 Z", BOOT) +
    h("path", { d: "M548 1054 C580 1048 612 1048 640 1052 L652 1102 L632 1106 L622 1072 C598 1070 572 1074 552 1078 Z", fill: "url(#cs-hatch-fine)", filter: "url(#cs-rough)" }) +
    g(INKG,
      h("path", { d: "M548 1054 C580 1048 612 1048 640 1052 L652 1102 L632 1106" }),
      h("path", { d: "M540 1060 C574 1052 612 1054 646 1060 L662 1108 M546 1086 C572 1082 602 1078 628 1080 L640 1112" }),
      h("path", { d: "M636 1108 L678 1108 L678 1096 L662 1092 L640 1096" }),
      h("path", { d: "M648 1098 l6 -4 M656 1100 l6 -4", "stroke-width": 1.4 }),
    ) +
    // 鞋底、鞋底紋、褲子接縫與膝蓋的摺痕、坐下時大腿被椅面壓出的皺褶
    fillPath("M636 1106 L680 1106 L680 1112 L636 1112 Z", "#3a2a20") +
    fillPath("M636 1104 L646 1104 L646 1112 L636 1112 Z", "#2e2119") +
    inkPath("M638 1090 C646 1094 654 1094 660 1092", 0.9, { opacity: 0.7 }) +
    inkPath("M642 1109 l3 3 M650 1109 l3 3 M658 1109 l3 3 M666 1109 l3 3", 0.8) +
    inkPath("M666 1106 C668 1100 674 1098 678 1100 M650 1098 l6 4 M656 1096 l-4 5", 0.9) +
    inkPath("M556 1068 C586 1064 612 1064 632 1068 M614 1062 C620 1068 622 1074 628 1078 M626 1058 C634 1064 636 1070 640 1080 M646 1066 C650 1076 652 1086 656 1098", 1.1) +
    inkPath("M560 1080 q6 -4 12 0 M580 1078 q6 -4 12 0", 0.9);
  const body =
    fillPath("M512 1066 C502 1032 506 996 520 974 C534 952 574 950 590 970 C606 992 606 1032 600 1066 Z", JACKET) +
    fillPath("M522 1030 L548 1030 L548 1044 L522 1044 Z", JACKET_SHADE) +
    fillPath("M526 964 C542 974 566 974 584 964 L588 978 C566 990 540 988 522 978 Z M538 984 C536 996 532 1008 534 1020 L546 1018 C544 1006 546 994 548 986 Z", SCARF) +
    h("path", { d: "M512 1066 C502 1032 506 996 520 974 L534 970 C524 1002 526 1038 536 1066 Z", fill: "url(#cs-hatch)", filter: "url(#cs-rough)" }) +
    g(INKG,
      h("path", { d: "M512 1066 C502 1032 506 996 520 974 C534 952 574 950 590 970 C606 992 606 1032 600 1066" }),
      h("path", { d: "M556 980 L558 1060", "stroke-width": 1.6 }),
      h("path", { d: "M522 1030 L548 1030 L548 1044 L522 1044 Z", "stroke-width": 1.6 }),
      h("path", { d: "M526 964 C542 974 566 974 584 964 L588 978 C566 990 540 988 522 978 Z M538 984 C536 996 532 1008 534 1020 M546 1018 C544 1006 546 994 548 986", "stroke-width": 2 }),
    ) +
    // 外套細節：肩線（raglan）、拉鍊齒與拉頭、口袋蓋與釦子、下襬收邊、腰間摺痕
    fillPath("M510 1056 C530 1060 580 1060 602 1056 L602 1066 L512 1066 Z", JACKET_SHADE) +
    inkPath("M512 1056 C534 1060 580 1060 601 1056", 1.2) +
    inkPath("M528 974 C530 994 526 1014 518 1030 M586 974 C592 990 596 1004 598 1016", 1.1) +
    inkPath("M555 986 l4 0 M555 994 l4 0 M556 1002 l4 0 M556 1010 l4 0 M556 1018 l4 0 M557 1026 l4 0 M557 1034 l4 0 M557 1042 l4 0 M557 1050 l4 0", 0.8) +
    shape("M552 984 L562 984 L562 994 L552 994 Z", "#c9c2b4", 1.1) +
    inkPath("M522 1036 L548 1036", 1) +
    h("circle", { cx: 535, cy: 1040, r: 1.6, fill: "#3a2a20" }) +
    inkPath("M540 1048 q8 4 14 0 M566 1046 q8 5 16 1", 0.9) +
    // 圍巾：針織橫紋與流蘇
    inkPath("M532 972 l2 6 M542 975 l1 7 M552 976 l0 7 M562 976 l-1 7 M572 974 l-2 6", 0.8) +
    inkPath("M535 1020 l-1 6 M539 1020 l0 6 M543 1019 l0 6", 0.9) +
    inkPath("M530 970 C542 980 564 980 580 970 M540 990 C539 1000 538 1008 539 1016 M544 990 C543 998 542 1006 542 1014", 0.8, { opacity: 0.7 }) +
    inkPath("M516 1000 q5 3 4 10 M598 1000 q-4 4 -3 10 M524 1016 q4 2 8 0", 0.8, { opacity: 0.7 });
  // 放在大腿上的左手（單手舉杯時才出現）
  const lapHand = g(
    { class: "cs-lap-lefthand", opacity: 0 },
    shape("M548 1044 C556 1038 570 1040 574 1048 C576 1056 566 1060 556 1058 C548 1056 544 1050 548 1044 Z", SKIN, 1.4),
    inkPath("M556 1046 l10 1 M555 1051 l11 1", 0.8),
  );
  const headArt = g({ transform: "translate(552 932)" }, head());
  const cup =
    fillPath("M572 1002 L612 1002 L608 1034 L576 1034 Z", "#f4efe4") +
    g({ transform: "translate(592 1018) scale(0.8)" }, h("use", { href: "#cs-mug-mark" })) +
    h("path", { d: "M578 1006 L576 1028", stroke: "#fff", "stroke-width": 3, "stroke-linecap": "round", opacity: 0.8 }) +
    g(INKG,
      h("path", { d: "M572 1002 L612 1002 L608 1034 L576 1034 Z" }),
      h("path", { d: "M612 1010 c12 0 12 16 -2 16" }),
    ) +
    // 左手從杯子左側托住：手掌在後、三根手指繞到杯身前面（單手舉杯時這隻手會放開、改放在大腿上）
    g(
      { class: "cs-cup-lefthand" },
      shape("M572 1010 C562 1012 560 1030 570 1034 L576 1034 L576 1010 Z", SKIN_SHADE, 1.4),
      shape("M570 1012 L585 1012 C589 1012 589 1018 585 1018 L570 1018 Z", SKIN, 1.1),
      shape("M569 1019 L587 1019 C591 1019 591 1025 587 1025 L569 1025 Z", SKIN, 1.1),
      shape("M570 1026 L584 1026 C588 1026 588 1031 584 1031 L570 1031 Z", SKIN, 1.1),
    ) +
    // 右手：袖口在杯把右下，四指勾住杯把、大拇指壓在杯把上緣
    shape("M620 1024 C628 1020 636 1026 634 1034 C632 1040 624 1040 620 1036 Z", JACKET_SHADE, 1.3) +
    shape("M612 1012 C620 1008 628 1014 627 1024 C626 1031 618 1032 613 1028 Z", SKIN, 1.3) +
    inkPath("M614 1016 l8 1 M613 1021 l9 1 M614 1026 l8 0", 0.8) +
    shape("M606 1008 C611 1003 619 1005 618 1010 C617 1013 610 1014 606 1011 Z", SKIN, 1.1) +

    g({ class: "cs-steam", filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-width": 1.8, "stroke-linecap": "round", opacity: 0.6 },
      h("path", { d: "M586 996 c-10 -16 10 -24 0 -40 c-10 -16 10 -24 0 -38" }),
      h("path", { d: "M600 994 c-10 -14 10 -22 0 -36" }),
    );
  // 袖子是一條從肩膀彎到手的粗線：杯子（連手）整個平移、袖子終點跟著走，杯子一直保持正的
  const rest = SIT_POSES.rest;
  const sleeveAttrs = { fill: "none", "stroke-linecap": "round", filter: "url(#cs-rough)" };
  const cupArm =
    h("path", { class: "cs-sleeve", d: rest.sleeve, stroke: INK, "stroke-width": 19, ...sleeveAttrs }) +
    h("path", { class: "cs-sleeve", d: rest.sleeve, stroke: "#b1573a", "stroke-width": 14, ...sleeveAttrs }) +
    g({ class: "cs-cup", transform: rest.cup }, cup);
  return g(
    { class: "cs-person cs-pose-sit", transform: "translate(-560 -1150)" },
    legs,
    body,
    lapHand,
    joint("cs-nod-head", 552, 960, 0, headArt),
    g({ class: "cs-cup-arm" }, cupArm),
  );
}

/**
 * 吊床上看星星的人（側面）：吊床內側布 → 躺著的人（頭朝左、臉朝上、蓋毯子）→ 吊床外側布蓋住下半身。
 * cs-hammock 繞掛點中間輕晃。原點在兩個掛點連線的中間。
 */
export function hammock(span: number): string {
  const half = span / 2;
  const sag = 130;
  const bottom = `C${-half * 0.5} ${sag * 1.15} ${half * 0.5} ${sag * 1.15} ${half} 0`;
  const inner = `M${-half} 0 ${bottom} C${half * 0.5} ${sag * 0.4} ${-half * 0.5} ${sag * 0.4} ${-half} 0 Z`;
  const outer = `M${-half} 0 ${bottom} C${half * 0.5} ${sag * 0.8} ${-half * 0.5} ${sag * 0.8} ${-half} 0 Z`;
  // 身體沿著吊床躺：肩膀在左、膝蓋微微拱起、腳在右；超出吊床的部分裁掉
  const blanket = `M${-half * 0.5} ${sag * 0.4} C${-half * 0.2} ${sag * 0.46} ${half * 0.05} ${sag * 0.5} ${half * 0.2} ${sag * 0.38} C${half * 0.3} ${sag * 0.28} ${half * 0.42} ${sag * 0.34} ${half * 0.55} ${sag * 0.36} L${half * 0.6} ${sag * 1.2} L${-half * 0.5} ${sag * 1.2} Z`;
  return g(
    { class: "cs-hammock" },
    h("clipPath", { id: "cs-hammock-clip" }, h("path", { d: inner })),
    inkPath(`M${-half} 0 L${-half - 22} -34 M${half} 0 L${half + 22} -34`, 1.8),
    fillPath(inner, "#3f7361"),
    // 傍晚躺著放鬆看天空（眼睛張開、胸前捧著杯子），不是睡覺
    g(
      { class: "cs-hammock-person" },
      g({ "clip-path": "url(#cs-hammock-clip)" }, shape(blanket, "#c9553a", 2.2, "cs-hatch-fine")),
      g({ transform: `translate(${-half * 0.58} ${sag * 0.3}) rotate(-80)` }, head(21, false)),
      g({ transform: `translate(${-half * 0.28} ${sag * 0.3})` }, shape("M-9 -12 L9 -12 L8 6 L-8 6 Z", "#f4efe4", 1.6), h("circle", { cx: -10, cy: 0, r: 5, fill: SKIN, stroke: INK, "stroke-width": 1.2 })),
    ),
    fillPath(outer, "#5fa38a"),
    h("path", { d: outer, fill: "url(#cs-hatch-fine)", filter: "url(#cs-rough)" }),
    inkPath(outer, 2.6),
    inkPath(`M${-half * 0.62} ${sag * 0.5} L${-half * 0.6} ${sag * 0.76} M${-half * 0.25} ${sag * 0.62} L${-half * 0.24} ${sag * 0.86} M${half * 0.12} ${sag * 0.64} L${half * 0.12} ${sag * 0.86} M${half * 0.5} ${sag * 0.5} L${half * 0.47} ${sag * 0.74}`, 1.2, { opacity: 0.5 }),
  );
}

/** 營火的火焰（會閃），原點在柴堆中心：幾條不對稱的火舌、內層黃、火心淡、線條用深紅棕而不是黑。 */
export function flame(scale = 1): string {
  const outer = "M-32 -6 C-40 -30 -26 -44 -24 -62 C-16 -50 -12 -46 -10 -56 C-8 -74 -2 -86 2 -108 C8 -86 14 -76 12 -58 C18 -64 22 -72 22 -84 C32 -64 38 -40 30 -6 Z";
  const mid = "M-18 -6 C-22 -24 -12 -34 -10 -48 C-4 -40 0 -42 2 -64 C8 -46 16 -36 14 -24 C18 -28 20 -32 20 -38 C26 -24 24 -14 18 -6 Z";
  const core = "M-8 -6 C-10 -18 -4 -24 -2 -36 C2 -26 8 -20 8 -6 Z";
  const edge = { fill: "none", stroke: "#8a3b1c", "stroke-linejoin": "round", "stroke-linecap": "round", filter: "url(#cs-rough)" };
  return g(
    { class: "cs-flame", transform: `scale(${scale})` },
    h("ellipse", { cx: 0, cy: -4, rx: 46, ry: 12, fill: "#f6b55a", opacity: 0.35 }),
    g({ class: "cs-flame-outer" }, fillPath(outer, "#ec7d2c"), h("path", { d: outer, ...edge, "stroke-width": 2 }), h("path", { d: "M-20 -30 C-16 -40 -14 -44 -12 -52 M16 -40 C20 -48 20 -56 20 -64", ...edge, "stroke-width": 1, opacity: 0.7 })),
    g({ class: "cs-flame-mid" }, fillPath(mid, "#f6b73f"), h("path", { d: mid, ...edge, stroke: "#c06a22", "stroke-width": 1.1, opacity: 0.8 })),
    g({ class: "cs-flame-core" }, fillPath(core, "#fff1b8")),
    g({ class: "cs-sparks" }, h("circle", { cx: -16, cy: -120, r: 2.2, fill: "#f7b33c" }), h("circle", { cx: 14, cy: -134, r: 1.8, fill: "#f7b33c" }), h("circle", { cx: 4, cy: -152, r: 1.4, fill: "#f7b33c" })),
  );
}

/**
 * 矩形天幕（兩主柱＋四角拉繩的「飛行式」搭法）。原點在兩柱中間的地面。
 * 前半片布面朝鏡頭斜下，邊緣受拉力往內彎、中間略有弧度；右側露出一點後半片的背光面。
 * cs-tarp-poles／cs-tarp-fabric／cs-tarp-line 讓時間軸做「立柱→展開→拉繩」。
 */
export const TARP_PTS = { L: [-200, -330], R: [210, -346], FL: [-372, -188], FR: [384, -204], BR: [318, -266], BL: [-322, -250] } as const;
export function tarpBulbs(): [number, number][] {
  const [x0, y0] = TARP_PTS.FL;
  const [x1, y1] = TARP_PTS.FR;
  const pts: [number, number][] = [];
  for (let i = 1; i < 10; i += 1) {
    const t = i / 10;
    pts.push([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t - 46 * 2 * t * (1 - t) + 20 + Math.sin(t * Math.PI) * 24]);
  }
  return pts;
}
export function tarp(): string {
  const { L, R, FL, FR, BR, BL } = TARP_PTS;
  const P = (p: readonly number[]) => `${p[0]} ${p[1]}`;
  // 前半片：脊線略拱、前緣往上彎（受拉力）、兩側往內彎
  // 脊線在兩柱間微微下垂；前緣受拉力明顯往上彎；兩側往內彎（布被四角拉繩拉緊的樣子）
  const ridgeQ = `Q${(L[0] + R[0]) / 2} ${(L[1] + R[1]) / 2 + 10}`;
  const sideR = `Q${R[0] + 96} ${(R[1] + FR[1]) / 2 + 14}`;
  const sideL = `Q${L[0] - 104} ${(L[1] + FL[1]) / 2 + 18}`;
  const frontQ = `Q${(FL[0] + FR[0]) / 2 + 20} ${(FL[1] + FR[1]) / 2 - 46}`;
  const front = `M${P(L)} ${ridgeQ} ${P(R)} ${sideR} ${P(FR)} ${frontQ} ${P(FL)} ${sideL} ${P(L)} Z`;
  // 左右兩端露出一點後半片（背光面）
  const back = `M${P(R)} Q${R[0] + 60} ${R[1] + 36} ${P(BR)} L${P(FR)} ${sideR.replace("Q", "Q")} ${P(R)} Z M${P(L)} Q${L[0] - 70} ${L[1] + 40} ${P(BL)} L${P(FL)} ${sideL} ${P(L)} Z`;
  let bulbs = "";
  for (const [x, y] of tarpBulbs()) bulbs += h("circle", { cx: x, cy: y, r: 5.5, fill: "#f7e3a0", stroke: INK, "stroke-width": 1.2 });
  const [sx0, sy0] = FL;
  const [sx1, sy1] = FR;
  // 電線順著每顆燈泡一段段垂下
  const pts: [number, number][] = [[sx0, sy0 + 4], ...tarpBulbs().map(([x, y]) => [x, y - 5] as [number, number]), [sx1, sy1 + 4]];
  let wire = `M${pts[0]![0]} ${pts[0]![1]}`;
  for (let i = 1; i < pts.length; i += 1) {
    const [ax, ay] = pts[i - 1]!;
    const [bx, by] = pts[i]!;
    wire += ` Q${((ax + bx) / 2).toFixed(1)} ${(Math.max(ay, by) + 7).toFixed(1)} ${bx.toFixed(1)} ${by.toFixed(1)}`;
  }
  const pole = (x: number, top: number) =>
    h("path", { d: `M${x} 0 L${x} ${top}`, stroke: "#5b5f63", "stroke-width": 6, "stroke-linecap": "round" }) +
    h("path", { d: `M${x - 1.5} -4 L${x - 1.5} ${top + 6}`, stroke: "#b8bcbf", "stroke-width": 1.4 }) +
    inkPath(`M${x - 5} ${top * 0.34} l10 0 M${x - 5} ${top * 0.67} l10 0 M${x} ${top} l0 -16`, 1.4);
  const line = (x0: number, y0: number, x1: number, y1: number) =>
    h("path", { class: "cs-tarp-line", d: `M${x0} ${y0} L${x1} ${y1}`, stroke: INK_SOFT, "stroke-width": 1.3, fill: "none", "stroke-dasharray": 600, "stroke-dashoffset": 0 }) +
    inkPath(`M${x1 - 2} ${y1 - 6} l4 14`, 2.2);
  return g(
    { class: "cs-tarp" },
    g({ class: "cs-tarp-shadow" }, shadow(10, 6, 380, 26, 0.16)),
    g(
      { class: "cs-tarp-lines" },
      line(L[0], L[1], -470, 8),
      line(R[0], R[1], 480, 4),
      line(FL[0], FL[1], -430, 46),
      line(FR[0], FR[1], 450, 44),
      line(BR[0], BR[1], 420, -16),
    ),
    g({ class: "cs-tarp-poles" }, pole(L[0], L[1]), pole(R[0], R[1])),
    g(
      { class: "cs-tarp-fabric" },
      fillPath(back, "#a88a5e"),
      hatchArea(back, "cs-hatch", 0.9),
      inkPath(back, 1.8),
      fillPath(front, "#d9bd8c"),
      h("path", { d: front, fill: "url(#cs-fabric)", opacity: 0.8 }),
      // 越靠近前緣越暗（布面往下斜、背光）
      h("path", { d: `M${P(FL)} ${frontQ.replace("Q", "Q")} ${P(FR)} L${FR[0] - 36} ${FR[1] - 36} Q${(FL[0] + FR[0]) / 2 + 20} ${(FL[1] + FR[1]) / 2 - 84} ${FL[0] + 40} ${FL[1] - 32} Z`, fill: "#b99a68", opacity: 0.55 }),
      // 布面張力的皺褶：從柱頂往四角放射
      inkPath(`M${L[0] + 10} ${L[1] + 12} Q${L[0] - 40} ${L[1] + 70} ${FL[0] + 30} ${FL[1] - 12} M${R[0] - 6} ${R[1] + 12} Q${R[0] + 40} ${R[1] + 70} ${FR[0] - 28} ${FR[1] - 14} M${(L[0] + R[0]) / 2} ${(L[1] + R[1]) / 2 + 4} Q${(L[0] + R[0]) / 2 + 4} ${(L[1] + R[1]) / 2 + 60} ${(FL[0] + FR[0]) / 2} ${(FL[1] + FR[1]) / 2 - 22}`, 1, { opacity: 0.55 }),
      // 脊線車縫、角落補強片、邊緣收邊
      inkPath(`M${P(L)} ${ridgeQ} ${P(R)}`, 1, { "stroke-dasharray": "5 5", opacity: 0.7, transform: "translate(0 7)" }),
      shape(`M${FL[0]} ${FL[1]} l26 -4 l-12 -20 Z M${FR[0]} ${FR[1]} l-26 -4 l12 -20 Z`, "#9c7c4f", 1.2),
      // 柱頂穿出布面的尖端
      inkPath(`M${L[0]} ${L[1]} l0 -18 M${R[0]} ${R[1]} l0 -18`, 2.4),
      inkPath(front, 2.4),
    ),
    // 串燈沿著前緣垂下（光暈在燈光層）
    g({ class: "cs-tarp-lights" }, h("path", { d: wire, stroke: INK, "stroke-width": 1.1, fill: "none" }), bulbs),
  );
}

/** 復古煤油暖爐（圓筒、玻璃窗裡的火、頂部散熱罩與提把）。原點在底部中央；cs-heater-flame 點燃才出現。 */
export function heater(): string {
  return g(
    { class: "cs-heater" },
    shadow(0, 2, 34, 6, 0.28),
    shape("M-26 0 L26 0 L24 -8 L-24 -8 Z", "#3c4a3f", 1.8),
    shape("M-24 -8 L24 -8 L24 -72 L-24 -72 Z", "#4f6a57", 2.2),
    hatchArea("M8 -8 L24 -8 L24 -72 L8 -72 Z", "cs-hatch-fine", 0.9),
    h("path", { d: "M-18 -14 L-18 -66", stroke: "#8fae96", "stroke-width": 2.4, "stroke-linecap": "round", opacity: 0.7 }),
    shape("M-14 -30 L14 -30 L14 -52 L-14 -52 Z", "#2c2a28", 1.6),
    g({ class: "cs-heater-flame", opacity: 0 }, h("rect", { x: -13, y: -51, width: 26, height: 20, fill: "#f7a93c" }), h("path", { d: "M-10 -34 C-8 -44 -2 -46 0 -50 C2 -46 8 -44 10 -34 Z", fill: "#ffe7a6" })),
    shape("M-26 -72 L26 -72 L20 -86 L-20 -86 Z", "#9aa0a4", 1.8),
    inkPath("M-18 -76 L18 -76 M-16 -81 L16 -81", 1),
    inkPath("M-14 -86 C-14 -104 14 -104 14 -86", 1.8),
  );
}
