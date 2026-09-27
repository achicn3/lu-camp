// 露營動畫的時間軸與結帳銜接。
// 待機一輪約 93 秒：開門 → 開露營車上山 → 卸貨 → 搭帳篷 → 泡咖啡 → 懸崖看雲海 → 下雨、彩虹、烤棉花糖 → 星空吊床。
// 結帳（店主 2026-09-27：不要直接把動畫關掉）：不論播到哪，鏡頭都順順地帶回營桌、拉近，
// 客人坐著喝咖啡等結帳；成交或簽完名就舉杯、畫出「謝謝光臨」；結束後從泡咖啡那段接著播。
import { gsap } from "gsap";

import { SKY_STOPS } from "./defs";
import { SIT_POSES } from "./figures";
import { roadY } from "./panels";
import { PARALLAX, SPOTS, THANKS_Y } from "./world";

export type SceneMode = "idle" | "cart" | "celebrate" | "hidden";

export type CampingController = {
  setMode(mode: SceneMode): void;
  /** 購物車多了一件：客人點個頭。 */
  bump(): void;
  /** 跳到待機時間軸的某一秒並停住（截圖、煙霧測試用）。 */
  seek(seconds: number): void;
  destroy(): void;
};

type SkyKey = keyof typeof SKY_STOPS;

/** 天上的斜光帶：白天最明顯，傍晚淡掉，晚上沒有。 */
const LIGHTBANDS: Record<SkyKey, number> = { day: 0.3, dawn: 0.3, golden: 0.18, sunset: 0.06, night: 0 };

const TINT: Record<SkyKey, { backgroundColor: string; opacity: number }> = {
  day: { backgroundColor: "#f0c080", opacity: 0 },
  dawn: { backgroundColor: "#f0c080", opacity: 0 },
  golden: { backgroundColor: "#f0a050", opacity: 0.2 },
  sunset: { backgroundColor: "#b8506a", opacity: 0.3 },
  night: { backgroundColor: "#1b2a55", opacity: 0.6 },
};

/** 結帳時鏡頭的位置：營桌與喝咖啡的人落在畫面上方那扇窗裡。 */
const CART_CAM_X = 4000;
const CART_ZOOM = { scale: 1.3, x: -290, y: -930 };
/** 成交／簽完：謝謝卡片只佔底部，鏡頭退一點讓畫面鋪滿、天空留給「謝謝光臨」。 */
const CELEBRATE_ZOOM = { scale: 1.15, x: -150, y: -210 };
const DOOR_HANDLE = { x: 641, y: 940 };
/** 手的圖裡，手指握的位置（相對手那張圖的左上角）。 */
const HAND_GRIP = { x: 54, y: 60 };
const PARK_Y = 1262;

/**
 * 在已塞好 buildSceneHtml() 的 root 上建立動畫控制。initialMode 不是 idle 時（例如一組好就是結帳中），
 * 直接擺到營桌、不從開門播起。
 */
export function createCampingController(root: HTMLElement, reducedMotion: boolean, initialMode: SceneMode = "idle"): CampingController {
  const $ = <T extends Element = HTMLElement>(sel: string): T => {
    const el = root.querySelector<T>(sel);
    if (!el) throw new Error(`camping scene: missing ${sel}`);
    return el;
  };
  const $$ = (sel: string): Element[] => Array.from(root.querySelectorAll(sel));

  const stage = $(".cs-stage");
  const zoom = $(".cs-zoom");
  const far = $(".cs-far");
  const mid = $(".cs-mid");
  const track = $(".cs-track");
  const lights = $(".cs-lights");
  const van = $(".cs-van-actor");
  const wheels = $$(".cs-wheel");
  const vanBody = $(".cs-van-body");
  const skyTop = $("#cs-sky-top");
  const skyBottom = $("#cs-sky-bottom");
  const tint = $(".cs-tint");

  // ── 舞台等比放大、貼齊底部 ──
  const fit = () => {
    const w = root.clientWidth || 1000;
    const hgt = root.clientHeight || 1400;
    const s = Math.max(w / 1000, hgt / 1400);
    gsap.set(stage, { x: (w - 1000 * s) / 2, scale: s, transformOrigin: "0% 100%" });
  };
  fit();
  const resize = typeof ResizeObserver === "function" ? new ResizeObserver(fit) : null;
  resize?.observe(root);

  // ── 角色放在世界座標 ──
  const offset = (el: Element) => ({ ox: Number((el as HTMLElement).dataset.ox ?? 0), oy: Number((el as HTMLElement).dataset.oy ?? 0) });
  const at = (el: Element, x: number, y: number) => {
    const { ox, oy } = offset(el);
    return { x: x - ox, y: y - oy };
  };
  const place = (sel: string, x: number, y: number) => {
    const el = $(sel);
    gsap.set(el, at(el, x, y));
  };

  // ── 鏡頭與露營車（每幀由 ticker 依代理值算出） ──
  const cam = { x: 0 };
  const vanState = { x: SPOTS.vanStartX };
  const vanOff = offset(van);
  const setFar = gsap.quickSetter(far, "x", "px");
  const setMid = gsap.quickSetter(mid, "x", "px");
  const setTrack = gsap.quickSetter(track, "x", "px");
  const setLights = gsap.quickSetter(lights, "x", "px");
  let lastWheel = Number.NaN;
  const tick = () => {
    setFar(-cam.x * PARALLAX.far);
    setMid(-cam.x * PARALLAX.mid);
    setTrack(-cam.x);
    setLights(-cam.x);
    const x = vanState.x;
    const blend = Math.min(1, Math.max(0, (x - 3000) / 200));
    const y = roadY(x) * (1 - blend) + PARK_Y * blend;
    const slope = (roadY(x + 40) - roadY(x - 40)) / 80;
    gsap.set(van, { x: x - vanOff.ox, y: y - vanOff.oy, rotation: (Math.atan(slope) * 180 * (1 - blend)) / Math.PI, transformOrigin: `${vanOff.ox}px ${vanOff.oy}px` });
    if (x !== lastWheel) {
      const deg = ((x / 34) * 180) / Math.PI;
      for (const wheel of wheels) wheel.setAttribute("transform", `rotate(${deg.toFixed(1)})`);
      vanBody.setAttribute("transform", `translate(0 ${(Math.sin(x / 23) * 1.6 * (1 - blend)).toFixed(2)})`);
      lastWheel = x;
    }
  };
  gsap.ticker.add(tick);

  // ── 常用片段 ──
  const skyTo = (tl: gsap.core.Timeline, key: SkyKey, duration: number, pos: gsap.Position) => {
    const [top, bottom] = SKY_STOPS[key];
    tl.to(skyTop, { attr: { "stop-color": top }, duration, ease: "sine.inOut" }, pos);
    tl.to(skyBottom, { attr: { "stop-color": bottom }, duration, ease: "sine.inOut" }, pos);
    tl.to(tint, { ...TINT[key], duration, ease: "sine.inOut" }, pos);
    tl.to(".cs-lightbands", { opacity: LIGHTBANDS[key], duration, ease: "sine.inOut" }, pos);
  };
  const poses = [".cs-walker", ".cs-hammerer", ".cs-sitter", ".cs-cliffsitter", ".cs-roaster"];
  const showPose = (tl: gsap.core.Timeline, sel: string | null, pos: gsap.Position) => {
    for (const p of poses) tl.set(p, { autoAlpha: p === sel ? 1 : 0 }, pos);
  };
  const rot = (deg: number) => ({ attr: { transform: `rotate(${deg})` } });
  /** 喝咖啡的人把杯子移到某個位置（杯子平移、袖子跟著彎）。 */
  const cupTo = (tl: gsap.core.Timeline, pose: keyof typeof SIT_POSES, duration: number, pos: gsap.Position, ease = "power2.inOut") => {
    tl.to(".cs-cup", { attr: { transform: SIT_POSES[pose].cup }, duration, ease }, pos);
    tl.to(".cs-sleeve", { attr: { d: SIT_POSES[pose].sleeve }, duration, ease }, pos);
  };
  /** 走路：腿與手臂前後擺、身體上下晃；走完回正。 */
  const walk = (tl: gsap.core.Timeline, from: [number, number], to: [number, number], duration: number, pos: number) => {
    const el = $(".cs-walker");
    tl.fromTo(el, at(el, from[0], from[1]), { ...at(el, to[0], to[1]), duration, ease: "none" }, pos);
    const step = 0.28;
    const repeat = Math.max(1, Math.round(duration / step) - 1);
    const swing = (sel: string, a: number) => tl.fromTo(`.cs-walker ${sel}`, rot(-a), { ...rot(a), duration: step, repeat, yoyo: true, ease: "sine.inOut" }, pos);
    swing(".cs-leg-back", 22);
    swing(".cs-leg-front", -22);
    swing(".cs-arm-back", -18);
    swing(".cs-arm-front", 14);
    tl.fromTo(".cs-walker .cs-bob", { y: 0 }, { y: -4, duration: step / 2, repeat: repeat * 2 + 1, yoyo: true, ease: "sine.inOut" }, pos);
    tl.set([".cs-walker .cs-leg-back", ".cs-walker .cs-leg-front", ".cs-walker .cs-arm-back", ".cs-walker .cs-arm-front"], rot(0), pos + duration);
  };
  const camTo = (tl: gsap.core.Timeline, x: number, duration: number, pos: gsap.Position, ease = "power1.inOut") => tl.to(cam, { x, duration, ease }, pos);

  // ── 待機主時間軸 ──
  const master = gsap.timeline({ repeat: -1, paused: true });
  const resetAll = (tl: gsap.core.Timeline) => {
    tl.set(cam, { x: 0 }, 0);
    tl.set(vanState, { x: SPOTS.vanStartX }, 0);
    tl.set(".cs-door", { autoAlpha: 1, scale: 1, transformOrigin: `${DOOR_HANDLE.x}px ${DOOR_HANDLE.y}px` }, 0);
    tl.set(".cs-door-leaf", { rotationY: 0 }, 0);
    tl.set(".cs-door-hand", { x: 1000, y: 1420, autoAlpha: 1 }, 0);
    tl.set(".cs-flash", { opacity: 1 }, 0);
    tl.set(".cs-van-door", { x: 0 }, 0);
    tl.set(".cs-driver", { autoAlpha: 1 }, 0);
    tl.set(".cs-item", { autoAlpha: 0 }, 0);
    tl.set(".cs-walker .cs-tent-bag", { autoAlpha: 1 }, 0);
    tl.set(".cs-walker .cs-mug", { autoAlpha: 0 }, 0);
    tl.set(".cs-tent-body", { scaleY: 0.02, scaleX: 0.3, transformOrigin: "50% 100%" }, 0);
    tl.set(".cs-tent-shadow", { scaleX: 0.2, transformOrigin: "50% 50%" }, 0);
    tl.set(".cs-peg", { y: 0 }, 0);
    tl.set(".cs-hammer-arm", rot(-150), 0);
    tl.set(".cs-cup", { attr: { transform: SIT_POSES.rest.cup } }, 0);
    tl.set(".cs-sleeve", { attr: { d: SIT_POSES.rest.sleeve } }, 0);
    tl.set(".cs-nod-head", rot(0), 0);
    tl.set(".cs-point-arm", rot(-20), 0);
    tl.set(".cs-stick-arm", rot(-62), 0);
    tl.set(".cs-marsh", { scale: 1, transformOrigin: "50% 50%" }, 0);
    tl.set(".cs-marsh-fire", { opacity: 0 }, 0);
    tl.set(".cs-marsh-body", { attr: { fill: "#fbf6ee" } }, 0);
    tl.set(".cs-fire-evening", { scale: 0, transformOrigin: "60px 180px" }, 0);
    tl.set(skyTop, { attr: { "stop-color": SKY_STOPS.day[0] } }, 0);
    tl.set(skyBottom, { attr: { "stop-color": SKY_STOPS.day[1] } }, 0);
    tl.set(tint, TINT.day, 0);
    tl.set(".cs-lightbands", { opacity: LIGHTBANDS.day }, 0);
    tl.set(".cs-balloon", at($(".cs-balloon"), 1150, 330), 0);
    tl.set(".cs-night", { opacity: 0 }, 0);
    tl.set(".cs-lights-wrap", { opacity: 0 }, 0);
    tl.set(".cs-rain", { opacity: 0 }, 0);
    tl.set(".cs-rainbow", { autoAlpha: 0 }, 0);
    tl.set(".cs-rainbow-band", { strokeDasharray: 1400, strokeDashoffset: 1400 }, 0);
    tl.set(".cs-constellation", { opacity: 0 }, 0);
    tl.set(".cs-const-line", { strokeDasharray: "1600", strokeDashoffset: 1600 }, 0);
    tl.set(".cs-const-star", { scale: 0, transformOrigin: "50% 50%" }, 0);
    tl.set(".cs-night-word", { scale: 0.3, opacity: 0, transformOrigin: "50% 50%" }, 0);
    tl.set(".cs-fireflies", { opacity: 0 }, 0);
    tl.set(".cs-sun", at($(".cs-sun"), 770, 250), 0);
    tl.set(".cs-moon", { ...at($(".cs-moon"), 230, 760), autoAlpha: 0 }, 0);
    tl.set(".cs-birds", { ...at($(".cs-birds"), -200, 380) }, 0);
    showPose(tl, null, 0);
  };
  resetAll(master);

  // ⓪ 開門（0–4.3s）：從白光淡入露坑店門口，鏡頭慢慢推近，一隻手推開門，走進光裡。
  master.to(".cs-flash", { opacity: 0, duration: 0.9, ease: "sine.out" }, 0);
  master.to(".cs-door", { scale: 1.1, duration: 3, ease: "sine.inOut" }, 0);
  master.to(".cs-door-hand", { x: DOOR_HANDLE.x - HAND_GRIP.x, y: DOOR_HANDLE.y - HAND_GRIP.y, duration: 1.3, ease: "power2.out" }, 0.8);
  master.to(".cs-door-hand", { x: "-=14", duration: 0.25, ease: "power1.inOut", yoyo: true, repeat: 1 }, 2.1);
  master.to(".cs-door-leaf", { rotationY: -100, duration: 1.6, ease: "power2.in" }, 2.5);
  master.to(".cs-door-hand", { x: "+=40", y: "+=30", autoAlpha: 0, duration: 0.9, ease: "power2.in" }, 2.9);
  master.to(".cs-door", { scale: 1.9, duration: 1.3, ease: "power2.in" }, 3.1);
  master.to(".cs-flash", { opacity: 1, duration: 0.5, ease: "sine.in" }, 3.6);
  master.set(".cs-door", { autoAlpha: 0 }, 4.1);
  master.to(".cs-flash", { opacity: 0, duration: 1.1, ease: "sine.out" }, 4.1);

  // ① 開露營車上山（4.1–21.5s）：鏡頭跟著車往右，車沿路面起伏、輪子轉、排氣一團團。
  master.addLabel("drive", 4.1);
  master.to(cam, { x: 3000, duration: 17.4, ease: "power1.inOut" }, 4.1);
  master.to(vanState, { x: SPOTS.vanParkX, duration: 17.4, ease: "power1.inOut" }, 4.1);
  master.fromTo(".cs-exhaust circle", { opacity: 0.8, x: 0, y: 0, scale: 0.4, transformOrigin: "50% 50%" }, { opacity: 0, x: -40, y: -20, scale: 1.3, duration: 1.1, stagger: 0.4, repeat: 12, ease: "sine.out" }, 4.4);
  master.to(".cs-birds", { ...at($(".cs-birds"), 1150, 300), duration: 9, ease: "none" }, 7);
  // 熱氣球慢慢從右飄到左，陪整個白天
  master.to(".cs-balloon", { ...at($(".cs-balloon"), -150, 250), duration: 50, ease: "none" }, 6);

  // ② 卸貨（21.5–27s）：側門滑開，保冷箱、紙箱、營燈一件件跳出來，人帶著帳篷袋下車。
  master.addLabel("unload", 21.5);
  master.to(".cs-van-door", { x: -118, duration: 0.7, ease: "power2.inOut" }, 21.7);
  const hop = (sel: string, toX: number, toY: number, pos: number) => {
    const el = $(sel);
    const start = at(el, SPOTS.vanParkX + 25, 1190);
    const end = at(el, toX, toY);
    master.fromTo(el, { ...start, autoAlpha: 0, scale: 0.7, transformOrigin: "50% 100%" }, { x: end.x, autoAlpha: 1, scale: 1, duration: 0.8, ease: "none" }, pos);
    master.to(el, { y: start.y - 90, duration: 0.35, ease: "power2.out" }, pos);
    master.to(el, { y: end.y, duration: 0.45, ease: "bounce.out" }, pos + 0.35);
  };
  hop(".cs-item-cooler", 3240, 1338, 22.4);
  hop(".cs-item-box", 3330, 1346, 23);
  hop(".cs-item-lantern", 3150, 1332, 23.6);
  master.set(".cs-driver", { autoAlpha: 0 }, 24.2);
  showPose(master, ".cs-walker", 24.2);
  walk(master, [SPOTS.vanParkX + 30, 1262], [3470, 1320], 1.4, 24.2);

  // ③ 搭帳篷（27–38.6s）：邊走邊跟著鏡頭到草地，帳篷「啪」地撐開，蹲下來敲營釘，再走去坐下。
  master.addLabel("tent", 27);
  camTo(master, 3850, 4.5, 27);
  walk(master, [3470, 1320], [3935, 1160], 4.5, 27);
  master.set(".cs-walker .cs-tent-bag", { autoAlpha: 0 }, 31.6);
  master.to(".cs-tent-body", { scaleY: 1, scaleX: 1, duration: 1.3, ease: "elastic.out(1, 0.45)" }, 31.7);
  master.to(".cs-tent-shadow", { scaleX: 1, duration: 0.8, ease: "power2.out" }, 31.7);
  showPose(master, ".cs-hammerer", 33.2);
  for (let i = 0; i < 4; i += 1) {
    const t = 33.4 + i * 0.7;
    master.to(".cs-hammer-arm", { ...rot(-37), duration: 0.2, ease: "power3.in" }, t);
    master.to(".cs-peg:first-child", { y: `+=${3}`, duration: 0.08 }, t + 0.2);
    master.to(".cs-hammer-arm", { ...rot(-150), duration: 0.4, ease: "power2.out" }, t + 0.28);
  }
  showPose(master, ".cs-walker", 36.4);
  camTo(master, 4000, 2.2, 36.4);
  walk(master, [3935, 1160], [4480, 1150], 2.2, 36.4);

  // ④ 泡咖啡（38.6–49s）：坐進露營椅，捧著露坑的杯子喝一口、點點頭。結帳結束後從這裡接著播。
  master.addLabel("coffee", 38.6);
  showPose(master, ".cs-sitter", 38.6);
  const sip = (t: number) => {
    cupTo(master, "sip", 0.9, t);
    master.to(".cs-nod-head", { ...rot(-6), duration: 0.6, ease: "sine.inOut" }, t + 0.5);
    master.to(".cs-nod-head", { ...rot(0), duration: 0.6, ease: "sine.inOut" }, t + 1.9);
    cupTo(master, "rest", 0.9, t + 2.1);
  };
  sip(39.6);
  master.to(".cs-nod-head", { ...rot(8), duration: 0.35, yoyo: true, repeat: 3, ease: "sine.inOut" }, 43.4);
  sip(45);

  // ⑤ 懸崖看雲海（49–63s）：拿著杯子走到崖邊，天色轉金黃，雲海在腳下流，伸手指遠方。
  master.addLabel("cliff", 49);
  master.set(".cs-walker .cs-mug", { autoAlpha: 1 }, 49);
  showPose(master, ".cs-walker", 49);
  camTo(master, 5000, 4.5, 49);
  // 從木牌前面（較靠近鏡頭）走過，不要穿過木牌
  walk(master, [4600, 1150], [5215, 1096], 4.5, 49);
  showPose(master, ".cs-cliffsitter", 53.5);
  skyTo(master, "golden", 5, 51);
  master.to(".cs-sun", { ...at($(".cs-sun"), 760, 560), duration: 10, ease: "sine.inOut" }, 51);
  master.to(".cs-point-arm", { ...rot(-118), duration: 0.8, ease: "back.out(1.6)" }, 56);
  master.to(".cs-point-arm", { ...rot(-20), duration: 0.8, ease: "power2.inOut" }, 59);
  master.fromTo(".cs-birds", at($(".cs-birds"), -200, 520), { ...at($(".cs-birds"), 1150, 420), duration: 8, ease: "none" }, 54);

  // ⑥ 傍晚（63–79s）：走向天幕途中下起雨，躲進天幕；雨停出彩虹，營火點起來、串燈亮，烤棉花糖烤到著火，吹熄吃掉。
  master.addLabel("evening", 63);
  showPose(master, ".cs-walker", 63);
  camTo(master, 6000, 4.5, 63);
  walk(master, [5270, 1094], [6300, 1244], 4.5, 63);
  skyTo(master, "sunset", 12, 63);
  master.to(".cs-sun", { ...at($(".cs-sun"), 700, 930), duration: 12, ease: "sine.in" }, 63);
  master.to(".cs-rain", { opacity: 1, duration: 0.8 }, 64.6);
  master.to(".cs-rain", { opacity: 0, duration: 1 }, 70);
  master.set(".cs-rainbow", { ...at($(".cs-rainbow"), 520, 900), autoAlpha: 1 }, 70.8);
  master.to(".cs-rainbow-band", { strokeDashoffset: 0, duration: 1.6, stagger: 0.12, ease: "power2.out" }, 70.8);
  master.to(".cs-rainbow", { autoAlpha: 0, duration: 1.5 }, 76.5);
  showPose(master, ".cs-roaster", 72);
  master.to(".cs-fire-evening", { scale: 1, duration: 0.8, ease: "back.out(2)" }, 72.2);
  master.to(".cs-lights-wrap", { opacity: 1, duration: 1.5 }, 72.4);
  master.to(".cs-marsh-fire", { opacity: 1, duration: 0.3 }, 74.2);
  master.to(".cs-marsh-body", { attr: { fill: "#6b4323" }, duration: 0.8 }, 74.2);
  master.to(".cs-stick-arm", { ...rot(-84), duration: 0.5, ease: "power2.out" }, 75.3);
  master.to(".cs-marsh-fire", { opacity: 0, duration: 0.2 }, 75.9);
  master.to(".cs-marsh", { scale: 0, duration: 0.3, ease: "back.in(2)" }, 76.8);
  master.to(".cs-stick-arm", { ...rot(-62), duration: 0.6, ease: "power2.inOut" }, 77.2);

  // ⑦ 星空（79–93s）：鏡頭到吊床，天黑、月亮升起、螢火蟲；星星連成露坑的 logo，再寫出「露坑」，流星劃過。
  master.addLabel("night", 79);
  camTo(master, 7000, 5, 79);
  skyTo(master, "night", 5, 79);
  master.to(".cs-night", { opacity: 1, duration: 4 }, 80);
  master.to(".cs-moon", { ...at($(".cs-moon"), 230, 290), autoAlpha: 1, duration: 6, ease: "sine.out" }, 80);
  master.to(".cs-fireflies", { opacity: 1, duration: 3 }, 82);
  master.set(".cs-constellation", { opacity: 1 }, 84);
  master.to(".cs-const-star", { scale: 1, duration: 0.5, stagger: 0.25, ease: "back.out(2.5)" }, 84);
  master.to(".cs-const-line", { strokeDashoffset: 0, duration: 2.4, ease: "power1.inOut" }, 84.3);
  master.to(".cs-night-word", { scale: 1, opacity: 1, duration: 1, ease: "back.out(1.8)" }, 87);
  master.fromTo(".cs-shooting", { opacity: 0, x: 0, y: 0 }, { opacity: 1, x: -220, y: 150, duration: 0.9, ease: "power2.in" }, 89);
  master.to(".cs-shooting", { opacity: 0, duration: 0.3 }, 89.9);
  master.to(".cs-flash", { opacity: 1, duration: 1.2, ease: "sine.in" }, 92);
  master.set({}, {}, 93.2);

  // ── 一直在動的小東西（不受結帳暫停影響） ──
  const ambient: gsap.core.Animation[] = [];
  const loop = (anim: gsap.core.Animation) => ambient.push(anim);
  loop(gsap.to(".cs-flame-outer", { scaleY: 1.12, scaleX: 0.94, transformOrigin: "50% 100%", duration: 0.3, repeat: -1, yoyo: true, ease: "sine.inOut" }));
  loop(gsap.to(".cs-flame-mid", { scaleY: 0.86, transformOrigin: "50% 100%", duration: 0.22, repeat: -1, yoyo: true, ease: "sine.inOut" }));
  loop(gsap.fromTo(".cs-sparks circle", { y: 0, opacity: 1 }, { y: -40, opacity: 0, duration: 1.4, stagger: 0.45, repeat: -1, ease: "sine.out" }));
  loop(gsap.fromTo(".cs-steam", { y: 4, opacity: 0.1 }, { y: -10, opacity: 0.65, duration: 1.6, repeat: -1, yoyo: true, ease: "sine.inOut" }));
  loop(gsap.to(".cs-sun-rays", { rotation: 360, svgOrigin: "0 0", duration: 60, repeat: -1, ease: "none" }));
  loop(gsap.to(".cs-flag", { skewY: 8, duration: 0.6, repeat: -1, yoyo: true, ease: "sine.inOut" }));
  loop(gsap.to(".cs-hammock", { rotation: 3, svgOrigin: "0 0", duration: 2.6, repeat: -1, yoyo: true, ease: "sine.inOut" }));
  loop(gsap.fromTo(".cs-rain svg", { y: -1400 }, { y: 0, duration: 0.9, repeat: -1, ease: "none" }));
  loop(gsap.to(".cs-lantern-glow, .cs-fire-glow, .cs-tentlamp-glow", { opacity: 0.75, duration: 0.8, repeat: -1, yoyo: true, stagger: 0.3, ease: "sine.inOut" }));
  $$(".cs-cloud").forEach((el, i) => {
    const y = [300, 200, 430, 540][i] ?? 300;
    const dur = 70 + i * 17;
    const tw = gsap.fromTo(el, at(el, -250, y), { ...at(el, 1250, y), duration: dur, repeat: -1, ease: "none" });
    tw.progress((i * 0.29 + 0.1) % 1);
    loop(tw);
  });
  $$(".cs-sea-cloud").forEach((el, i) => {
    const y = 1040 + i * 40;
    const dir = i % 2 === 0 ? 1 : -1;
    const x0 = -200;
    const x1 = SPOTS.seaClipW + 200;
    const tw = gsap.fromTo(el, at(el, dir > 0 ? x0 : x1, y), { ...at(el, dir > 0 ? x1 : x0, y), duration: 22 + i * 5, repeat: -1, ease: "none" });
    tw.progress((i * 0.37) % 1);
    loop(tw);
  });
  $$(".cs-firefly").forEach((el, i) => {
    loop(gsap.to(el, { x: `+=${20 + (i % 5) * 8}`, y: `-=${14 + (i % 3) * 10}`, duration: 2 + (i % 4) * 0.6, repeat: -1, yoyo: true, ease: "sine.inOut" }));
    loop(gsap.to(el, { opacity: 0.2, duration: 0.7 + (i % 3) * 0.4, repeat: -1, yoyo: true, ease: "sine.inOut" }));
  });
  $$(".cs-star").forEach((el, i) => {
    if (i % 3 === 0) loop(gsap.to(el, { opacity: 0.25, duration: 0.8 + (i % 5) * 0.3, repeat: -1, yoyo: true, ease: "sine.inOut" }));
  });

  // 從世界某處放上固定位置的角色
  place(".cs-tent-actor", SPOTS.tentX, 1120);
  place(".cs-fire-coffee", SPOTS.coffeeFireX, 1250);
  place(".cs-fire-evening", SPOTS.eveningFireX, 1260);
  place(".cs-hammerer", 3935, 1160);
  place(".cs-sitter", SPOTS.sitX, 1150);
  place(".cs-cliffsitter", SPOTS.cliffX, 1094);
  place(".cs-roaster", SPOTS.roastX, 1262);
  place(".cs-hammock-actor", SPOTS.hammockX, 1000);

  // ── 結帳銜接 ──
  let mode: SceneMode = "idle";
  /** 目前畫面是否停在「營桌拉近」的結帳狀態（離開時要從泡咖啡那段接回去）。 */
  let atTable = false;
  let transition: gsap.core.Timeline | null = null;
  let cartLoop: gsap.core.Timeline | null = null;

  const zoomFor = (m: SceneMode) => (m === "celebrate" ? CELEBRATE_ZOOM : CART_ZOOM);

  const goToTable = (instant: boolean) => {
    transition?.kill();
    const tr = gsap.timeline();
    const d = instant ? 0 : 1;
    const panDur = instant ? 0 : 1.2 + Math.min(2, Math.abs(cam.x - CART_CAM_X) / 2000);
    tr.to(".cs-door", { autoAlpha: 0, duration: 0.5 * d }, 0);
    tr.to(".cs-flash", { opacity: 0, duration: 0.5 * d }, 0);
    tr.to(cam, { x: CART_CAM_X, duration: panDur, ease: "power2.inOut" }, 0);
    tr.to(vanState, { x: SPOTS.vanParkX, duration: panDur, ease: "power2.inOut" }, 0);
    const [top, bottom] = SKY_STOPS.day;
    tr.to(skyTop, { attr: { "stop-color": top }, duration: 1.2 * d }, 0);
    tr.to(skyBottom, { attr: { "stop-color": bottom }, duration: 1.2 * d }, 0);
    tr.to(tint, { ...TINT.day, duration: 1.2 * d }, 0);
    tr.to(".cs-lightbands", { opacity: LIGHTBANDS.day, duration: 1.2 * d }, 0);
    tr.to([".cs-night", ".cs-rain", ".cs-lights-wrap"], { opacity: 0, duration: 0.8 * d }, 0);
    tr.to(".cs-rainbow", { autoAlpha: 0, duration: 0.5 * d }, 0);
    tr.to(".cs-sun", { ...at($(".cs-sun"), 770, 250), duration: 1.2 * d }, 0);
    tr.to(".cs-moon", { autoAlpha: 0, duration: 0.6 * d }, 0);
    tr.set([".cs-driver"], { autoAlpha: 0 }, 0);
    tr.set(".cs-item", { autoAlpha: 1 }, 0);
    for (const p of poses) tr.to(p, { autoAlpha: p === ".cs-sitter" ? 1 : 0, duration: 0.4 * d }, 0.2 * d);
    tr.to(".cs-tent-body", { scaleY: 1, scaleX: 1, duration: 0.9 * d, ease: "elastic.out(1, 0.5)" }, 0.2 * d);
    tr.to(".cs-tent-shadow", { scaleX: 1, duration: 0.5 * d }, 0.2 * d);
    cupTo(tr, "rest", 0.5 * d, 0);
    tr.to(zoom, { ...zoomFor(mode), transformOrigin: "0 0", duration: instant ? 0 : 1.6, ease: "power2.inOut" }, instant ? 0 : Math.max(0.2, panDur - 0.9));
    transition = tr;
    atTable = true;
  };

  const startCartLoop = () => {
    cartLoop?.kill();
    if (reducedMotion) return;
    const tl = gsap.timeline({ repeat: -1, delay: 2.5, repeatDelay: 3 });
    cupTo(tl, "sip", 0.9, 0);
    tl.to(".cs-nod-head", { ...rot(-6), duration: 0.6, ease: "sine.inOut" }, 0.5);
    tl.to(".cs-nod-head", { ...rot(0), duration: 0.6, ease: "sine.inOut" }, 1.9);
    cupTo(tl, "rest", 0.9, 2.1);
    cartLoop = tl;
  };

  const celebrate = () => {
    cartLoop?.kill();
    cartLoop = null;
    const tl = gsap.timeline({ delay: reducedMotion ? 0 : 0.6 });
    const d = reducedMotion ? 0 : 1;
    tl.set(".cs-thanks", { opacity: 1 }, 0);
    // y 寫死回原位：上一次慶祝的上下飄可能停在半路，不歸位會越飄越高
    tl.fromTo(".cs-thanks-word", { scale: 0.3, opacity: 0, y: THANKS_Y, transformOrigin: "50% 50%" }, { scale: 1, opacity: 1, y: THANKS_Y, duration: 0.9 * d, ease: "elastic.out(1, 0.55)" }, 0);
    tl.fromTo(".cs-thanks-star", { scale: 0, rotation: -90, transformOrigin: "50% 50%" }, { scale: 1, rotation: 0, duration: 0.6 * d, stagger: 0.12 * d, ease: "back.out(2.4)" }, 0.2 * d);
    cupTo(tl, "cheers", 0.6 * d, 0, "back.out(1.8)");
    tl.to(".cs-nod-head", { ...rot(-8), duration: 0.3 * d, yoyo: true, repeat: 3 }, 0.3 * d);
    if (!reducedMotion) {
      tl.to(".cs-thanks-star", { rotation: 20, duration: 1.2, yoyo: true, repeat: -1, ease: "sine.inOut", stagger: 0.2 }, 1.4);
      tl.to(".cs-thanks-word", { y: "-=8", duration: 1.4, yoyo: true, repeat: -1, ease: "sine.inOut" }, 1.4);
    }
    cartLoop = tl;
  };

  const backToIdle = () => {
    transition?.kill();
    cartLoop?.kill();
    cartLoop = null;
    const tr = gsap.timeline({
      onComplete: () => {
        // 從頭把時間軸「快轉」到泡咖啡那一刻，所有狀態都跟著對齊，再往下播
        master.pause();
        master.seek(0, true);
        master.seek("coffee", true);
        if (!reducedMotion) master.play();
      },
    });
    const d = reducedMotion ? 0 : 1;
    tr.to(".cs-thanks", { opacity: 0, duration: 0.6 * d }, 0);
    cupTo(tr, "rest", 0.6 * d, 0);
    tr.to(".cs-nod-head", { ...rot(0), duration: 0.3 * d }, 0);
    tr.to(zoom, { scale: 1, x: 0, y: 0, duration: 1.5 * d, ease: "power2.inOut" }, 0.2 * d);
    transition = tr;
    atTable = false;
  };

  const setMode = (next: SceneMode) => {
    if (next === mode) return;
    const prev = mode;
    mode = next;
    if (next === "hidden") {
      master.pause();
      return;
    }
    if (next === "idle") {
      if (atTable) backToIdle();
      else if (!reducedMotion) master.resume();
      return;
    }
    master.pause();
    if (!atTable) goToTable(reducedMotion || prev === "hidden");
    else gsap.to(zoom, { ...zoomFor(next), duration: reducedMotion ? 0 : 1.2, ease: "power2.inOut" });
    if (next === "cart") {
      gsap.to(".cs-thanks", { opacity: 0, duration: reducedMotion ? 0 : 0.4 });
      startCartLoop();
    } else {
      celebrate();
    }
  };

  if (reducedMotion) {
    // 系統要求減少動態：停在泡咖啡那一幕
    master.seek("coffee", true);
    master.seek("coffee+=1", true);
  } else {
    master.play(0);
  }
  if (initialMode !== "idle") {
    // 當作剛從隱藏狀態出來：直接擺到營桌，不必從開門那一幕一路搖鏡頭過去
    mode = "hidden";
    setMode(initialMode);
  }

  return {
    setMode,
    bump() {
      if (reducedMotion || mode !== "cart") return;
      gsap.fromTo(".cs-nod-head", rot(0), { ...rot(9), duration: 0.25, yoyo: true, repeat: 1, ease: "sine.inOut" });
    },
    seek(seconds) {
      master.pause();
      master.seek(0, true);
      master.seek(seconds, true);
      tick();
    },
    destroy() {
      gsap.ticker.remove(tick);
      resize?.disconnect();
      master.kill();
      transition?.kill();
      cartLoop?.kill();
      for (const a of ambient) a.kill();
    },
  };
}
