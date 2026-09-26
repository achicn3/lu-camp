// 會動的角色與道具：人物（各種姿勢）、露營車、帳篷。每個都有 class 讓時間軸抓得到要動的關節。
// 座標：人物原點在腳底中央、面向右；露營車原點在前後輪中間的地面。
import { INK, doodleText, fillPath, g, h, inkPath, shadow, shape } from "./svg";

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
  for (let x = -r; x < r + 4; x += 5) ribs += `M${x} -9 L${x} 2 `;
  const eyes = closedEyes ? `M8 4 q5 5 10 0 M-10 2 q4 4 8 0` : `M10 2 m-2 0 a2 2 0 1 0 4 0 a2 2 0 1 0 -4 0 M-6 1 m-2 0 a2 2 0 1 0 4 0 a2 2 0 1 0 -4 0`;
  const lines = g(
    { filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-width": 2.4, "stroke-linecap": "round", "stroke-linejoin": "round" },
    h("circle", { r }),
    h("ellipse", { cx: -r * 0.88, cy: 4, rx: 5, ry: 7, "stroke-width": 1.8 }),
    h("path", { d: `M${-r - 1} -6 C${-r - 3} ${-r - 16} ${r + 3} ${-r - 20} ${r + 3} -8` }),
    h("path", { d: `M${-r - 3} -8 L${r + 5} -10 L${r + 5} 2 L${-r - 3} 4 Z`, "stroke-width": 2 }),
    h("circle", { cx: 0, cy: -r - 17, r: 9, "stroke-width": 1.8 }),
    h("path", { d: ribs, "stroke-width": 1.2, opacity: 0.5 }),
    h("path", { d: eyes, "stroke-width": 1.8 }),
    h("path", { d: "M18 14 q4 3 8 0", "stroke-width": 1.6 }),
  );
  return face + ear + h("circle", { cx: 14, cy: 10, r: 4, fill: "#e89b86", opacity: 0.7 }) + hat + lines;
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
    )
  );
}

/** 站姿／走路的人：legs、arms 可擺動；hold 為前手拿的東西。 */
export function standingPerson(cls: string, hold = ""): string {
  return g(
    { class: `cs-person ${cls}` },
    shadow(0, 2, 40, 7),
    g({ class: "cs-bob" },
      g({ transform: "translate(-4 -78)" }, leg(78, "cs-leg-back", true)),
      g({ transform: "translate(0 -150)" }, arm(58, "cs-arm-back")),
      g({ transform: "translate(4 -78)" }, leg(78, "cs-leg-front")),
      g({ transform: "translate(0 -76)" }, torso()),
      g({ transform: "translate(8 -176)", class: "cs-head" }, head()),
      g({ transform: "translate(6 -146)" }, arm(58, "cs-arm-front", hold)),
    ),
  );
}

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
    g({ transform: "translate(-2 -40) rotate(28)" }, torso()),
    g({ transform: "translate(44 -142) rotate(12)", class: "cs-head" }, head()),
    g({ transform: "translate(30 -104)" }, g({ class: "cs-hammer-arm" }, arm(44, "cs-arm-hammer", mallet))),
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
        h("circle", { r: 16, fill: "#d9dbd6", stroke: INK, "stroke-width": 2 }),
        inkPath("M-16 0 L16 0 M0 -16 L0 16 M-11 -11 L11 11 M-11 11 L11 -11", 1.6),
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
      shape("M104 -190 L140 -190 C150 -176 164 -150 174 -128 L104 -128 Z", "#bfe0ea", 2.4),
      g({ class: "cs-driver", transform: "translate(126 -140)" }, h("circle", { cy: -18, r: 17, fill: SKIN, stroke: INK, "stroke-width": 2, filter: "url(#cs-rough)" }), fillPath("M-18 -22 C-20 -48 20 -48 20 -24 Z", BEANIE), inkPath("M-18 -22 C-20 -48 20 -48 20 -24", 2), inkPath("M4 -16 q4 4 8 0", 1.6)),
      h("path", { d: "M110 -184 L126 -184 L114 -150 Z", fill: "#fff", opacity: 0.7 }),
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
      inkPath(body, 3),
      inkPath("M-190 -104 L196 -104", 2),
      // 車燈、保險桿
      shape("M186 -96 C194 -96 198 -84 198 -76 L186 -76 Z", "#f7d67a", 2),
      shape("M-196 -52 L-184 -52 L-184 -36 L-196 -36 Z M190 -52 L206 -52 L206 -36 L190 -36 Z", "#9aa0a6", 2),
    ),
    wheel(-110),
    wheel(120),
    g({ class: "cs-exhaust", transform: "translate(-200 -40)" }, h("circle", { cx: -14, cy: -6, r: 10, fill: "#e8e2d6", stroke: INK, "stroke-width": 1.6, opacity: 0 }), h("circle", { cx: -30, cy: -14, r: 13, fill: "#e8e2d6", stroke: INK, "stroke-width": 1.6, opacity: 0 })),
  );
}

/** 帳篷：cs-tent-body 由收合（scaleY 0）撐開；原點在帳篷底中央。 */
export function tent(): string {
  const word = doodleText("露坑", { font: "marker", size: 80, fill: "#fff1d6", outline: "#3a2210", outlineWidth: 10, drop: 5 });
  return g(
    { class: "cs-tent" },
    g({ class: "cs-tent-shadow" }, shadow(10, 8, 210, 18, 0.18)),
    g(
      { class: "cs-tent-body" },
      fillPath("M-170 0 L0 -320 L170 0 Z", "#dca64a"),
      fillPath("M0 -320 L170 0 L240 0 L58 -320 Z", "#b97f2f"),
      fillPath("M-54 0 L0 -160 L54 0 Z", "#4a3322"),
      fillPath("M-24 0 L0 -110 L24 0 Z", "#2f2016"),
      fillPath("M-60 0 C-46 -60 -28 -120 0 -160 C-12 -116 -20 -60 -24 0 Z", "#e9bd67"),
      h("path", { d: "M0 -320 L170 0 L240 0 L58 -320 Z", fill: "url(#cs-hatch)", filter: "url(#cs-rough)" }),
      h("path", { d: "M-54 0 L0 -160 L54 0 Z", fill: "url(#cs-cross)", filter: "url(#cs-rough)", opacity: 0.7 }),
      g({ transform: "translate(2 -170) rotate(-7)" }, word, g({ transform: "translate(74 -58)" }, h("path", { d: "M0 -12 l3.5 10 l10 1 l-8 6 l2.5 10 l-8 -6 l-8 6 l2.5 -10 l-8 -6 l10 -1z", fill: "#e8674a", stroke: "#3a2210", "stroke-width": 2.2, filter: "url(#cs-rough)" }))),
      g(
        { filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-linecap": "round", "stroke-linejoin": "round" },
        h("path", { d: "M-170 0 L0 -320 L170 0", "stroke-width": 3.4 }),
        h("path", { d: "M0 -320 L58 -320 L240 0 L170 0", "stroke-width": 3 }),
        h("path", { d: "M-54 0 L0 -160 L54 0", "stroke-width": 2.6 }),
        h("path", { d: "M-60 0 C-46 -60 -28 -120 0 -160", "stroke-width": 2.2 }),
        h("path", { d: "M-85 -160 L0 -304 M85 -160 L10 -304", "stroke-width": 1.6, "stroke-dasharray": "6 6", opacity: 0.7 }),
        h("path", { d: "M-150 -14 L150 -14", "stroke-width": 1.4, "stroke-dasharray": "5 6", opacity: 0.6 }),
        h("path", { d: "M0 -320 L0 -368", "stroke-width": 3.2 }),
        h("path", { d: "M-170 0 L-226 30 M240 0 L300 26 M58 -320 L260 12", "stroke-width": 1.4 }),
      ),
      g({ class: "cs-flag", transform: "translate(0 -364)" }, fillPath("M0 0 L42 10 L0 24 Z", "#c9553a"), inkPath("M0 0 L42 10 L0 24", 1.8)),
    ),
    g({ class: "cs-pegs", filter: "url(#cs-rough)", stroke: INK, "stroke-width": 3, "stroke-linecap": "round" }, h("path", { class: "cs-peg", d: "M-230 22 l5 16" }), h("path", { class: "cs-peg", d: "M296 18 l5 16" }), h("path", { class: "cs-peg", d: "M256 4 l5 16" })),
  );
}

/** 繞 (px,py) 轉的關節：外層平移到支點，class 那層只放 rotate()，內層再平移回來。 */
function joint(cls: string, px: number, py: number, rotate: number, body: string): string {
  return g({ transform: `translate(${px} ${py})` }, g({ class: cls, transform: `rotate(${rotate})` }, g({ transform: `translate(${-px} ${-py})` }, body)));
}

/**
 * 坐在露營椅上喝咖啡的人（店主定稿樣張那一位）。沿用樣張的座標，原點移到椅腳中間 (560,1150)。
 * cs-cup-arm 舉杯喝／乾杯，cs-nod-head 點頭，cs-steam 冒煙。
 */
export function sittingPerson(): string {
  const INKG = { filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-width": 2.6, "stroke-linecap": "round", "stroke-linejoin": "round" };
  const legs =
    fillPath("M548 1054 C580 1048 612 1048 640 1052 L652 1102 L632 1106 L622 1072 C598 1070 572 1074 552 1078 Z", PANTS_SHADE) +
    fillPath("M540 1060 C574 1052 612 1054 646 1060 L662 1108 L640 1112 L628 1080 C602 1078 572 1082 546 1086 Z", PANTS) +
    fillPath("M636 1108 L678 1108 L678 1096 L662 1092 L640 1096 Z", BOOT) +
    h("path", { d: "M548 1054 C580 1048 612 1048 640 1052 L652 1102 L632 1106 L622 1072 C598 1070 572 1074 552 1078 Z", fill: "url(#cs-hatch-fine)", filter: "url(#cs-rough)" }) +
    g(INKG,
      h("path", { d: "M548 1054 C580 1048 612 1048 640 1052 L652 1102 L632 1106" }),
      h("path", { d: "M540 1060 C574 1052 612 1054 646 1060 L662 1108 M546 1086 C572 1082 602 1078 628 1080 L640 1112" }),
      h("path", { d: "M636 1108 L678 1108 L678 1096 L662 1092 L640 1096" }),
      h("path", { d: "M648 1098 l6 -4 M656 1100 l6 -4", "stroke-width": 1.4 }),
    );
  const body =
    fillPath("M512 1066 C502 1032 506 996 520 974 C534 952 574 950 590 970 C606 992 606 1032 600 1066 Z", JACKET) +
    fillPath("M522 1030 L548 1030 L548 1044 L522 1044 Z", JACKET_SHADE) +
    fillPath("M526 964 C542 974 566 974 584 964 L588 978 C566 990 540 988 522 978 Z M540 984 L534 1012 L546 1008 Z", SCARF) +
    h("path", { d: "M512 1066 C502 1032 506 996 520 974 L534 970 C524 1002 526 1038 536 1066 Z", fill: "url(#cs-hatch)", filter: "url(#cs-rough)" }) +
    g(INKG,
      h("path", { d: "M512 1066 C502 1032 506 996 520 974 C534 952 574 950 590 970 C606 992 606 1032 600 1066" }),
      h("path", { d: "M556 980 L558 1060", "stroke-width": 1.6 }),
      h("path", { d: "M522 1030 L548 1030 L548 1044 L522 1044 Z", "stroke-width": 1.6 }),
      h("path", { d: "M526 964 C542 974 566 974 584 964 L588 978 C566 990 540 988 522 978 Z M540 984 L534 1012 L546 1008", "stroke-width": 2 }),
    );
  const headArt = g({ transform: "translate(552 932)" }, head());
  const cupArm =
    fillPath("M580 978 C594 998 598 1016 588 1026 C580 1032 574 1024 580 1014 C586 1004 580 992 572 986 Z", "#b1573a") +
    fillPath("M572 1002 L612 1002 L608 1034 L576 1034 Z", "#f4efe4") +
    h("circle", { cx: 574, cy: 1022, r: 8, fill: SKIN, filter: "url(#cs-pencil)" }) +
    h("circle", { cx: 610, cy: 1016, r: 7, fill: SKIN, filter: "url(#cs-pencil)" }) +
    g({ transform: "translate(583 1010) scale(0.8)" }, h("use", { href: "#cs-mug-mark" })) +
    h("path", { d: "M578 1006 L576 1028", stroke: "#fff", "stroke-width": 3, "stroke-linecap": "round", opacity: 0.8 }) +
    g(INKG,
      h("path", { d: "M572 1002 L612 1002 L608 1034 L576 1034 Z" }),
      h("path", { d: "M612 1010 c12 0 12 16 -2 16" }),
      h("path", { d: "M580 978 C594 998 598 1016 588 1026", "stroke-width": 2 }),
    ) +
    g({ class: "cs-steam", filter: "url(#cs-rough)", fill: "none", stroke: INK, "stroke-width": 1.8, "stroke-linecap": "round", opacity: 0.6 },
      h("path", { d: "M586 996 c-10 -16 10 -24 0 -40 c-10 -16 10 -24 0 -38" }),
      h("path", { d: "M600 994 c-10 -14 10 -22 0 -36" }),
    );
  return g(
    { class: "cs-person cs-pose-sit", transform: "translate(-560 -1150)" },
    legs,
    body,
    joint("cs-nod-head", 552, 960, 0, headArt),
    joint("cs-cup-arm", 582, 980, 0, cupArm),
  );
}

/** 吊床上看星星的人：cs-hammock 繞兩端中點輕晃。原點在兩樹中間、吊床掛點高度。 */
export function hammock(span: number): string {
  const half = span / 2;
  const sag = 120;
  const bed = `M${-half} 0 C${-half * 0.5} ${sag} ${half * 0.5} ${sag} ${half} 0 C${half * 0.4} ${sag * 0.7} ${-half * 0.4} ${sag * 0.7} ${-half} 0 Z`;
  return g(
    { class: "cs-hammock" },
    fillPath(bed, "#5fa38a"),
    h("path", { d: bed, fill: "url(#cs-hatch-fine)", filter: "url(#cs-rough)" }),
    // 蓋毯子的人：頭在左、腳在右
    g({ transform: `translate(${-half * 0.45} ${sag * 0.52}) rotate(-8)` }, head(22)),
    shape(`M${-half * 0.3} ${sag * 0.6} C${-half * 0.1} ${sag * 0.36} ${half * 0.4} ${sag * 0.46} ${half * 0.55} ${sag * 0.52} C${half * 0.3} ${sag * 0.82} ${-half * 0.1} ${sag * 0.86} ${-half * 0.3} ${sag * 0.6} Z`, "#c9553a", 2.2, "cs-hatch-fine"),
    inkPath(bed, 2.6),
    inkPath(`M${-half} 0 L${-half - 20} -30 M${half} 0 L${half + 20} -30`, 1.6),
  );
}

/** 營火的火焰（會閃），原點在柴堆中心。 */
export function flame(scale = 1): string {
  return g(
    { class: "cs-flame", transform: `scale(${scale})` },
    g({ class: "cs-flame-outer" }, fillPath("M-30 -8 C-34 -50 -8 -62 0 -104 C8 -62 34 -50 30 -8 Z", "#f08a2c"), inkPath("M-30 -8 C-34 -50 -8 -62 0 -104 C8 -62 34 -50 30 -8", 2.4)),
    g({ class: "cs-flame-mid" }, fillPath("M-15 -8 C-18 -38 -4 -48 0 -76 C4 -48 18 -38 15 -8 Z", "#f7c64a")),
    g({ class: "cs-flame-core" }, fillPath("M-6 -8 C-7 -24 -2 -30 0 -46 C2 -30 7 -24 6 -8 Z", "#fff0b0")),
    g({ class: "cs-sparks" }, h("circle", { cx: -18, cy: -122, r: 2.4, fill: "#f7b33c" }), h("circle", { cx: 14, cy: -140, r: 2, fill: "#f7b33c" }), h("circle", { cx: 4, cy: -160, r: 1.6, fill: "#f7b33c" })),
  );
}
