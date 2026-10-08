// 手沖體驗卡（docs/63 §4、M1c；店主 2026-10-08 選定的展示稿）：牌組小卡 → 抽卡動畫 → 翻開 → 體驗內容。
// 效能：動畫只動 transform／opacity（Web Animations），插畫是少量圖形的 SVG；減少動態效果時直接翻開。
// 安全：CSP 不允許行內 style 屬性，配色用 class；店家填的文字一律 textContent，SVG 只用固定字串。
import { money, type ExperienceView } from "./logic";
import type { MenuExperienceView, OptionGroupView } from "./types";

type Effect = Exclude<MenuExperienceView["effect"], "random">;
const EFFECTS: Effect[] = ["soar", "truck", "smash", "seal", "shuffle", "bloom"];

let uid = 0;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
const wait = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));
const play = (node: Element, frames: Keyframe[], options: KeyframeAnimationOptions) =>
  node.animate(frames, { fill: "forwards", ...options }).finished.then(() => undefined, () => undefined);

// ── 插畫（Codex 繪製的水彩 JPG，放 public/brew/；店家只能從固定清單選，不接受任意網址）──
const ART: Record<string, string> = {
  peach: "/brew/peach.jpg",
  vanilla: "/brew/vanilla.jpg",
  citrus: "/brew/citrus.jpg",
  rum: "/brew/rum.jpg",
};

function svg(markup: string, viewBox: string, className: string): SVGSVGElement {
  // 固定字串（不含任何店家輸入）；漸層 id 加流水號，免得同頁多張卡互相覆蓋。
  const id = ++uid;
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("viewBox", viewBox);
  node.setAttribute("class", className);
  node.setAttribute("aria-hidden", "true");
  node.innerHTML = markup.replace(/id="(\w+)"/g, `id="$1-${id}"`).replace(/url\(#(\w+)\)/g, `url(#$1-${id})`);
  return node;
}
function art(experience: MenuExperienceView): HTMLImageElement | null {
  const src = ART[experience.art];
  if (!src) return null;
  const image = el("img", "brew-art");
  image.src = src;
  image.alt = "";
  image.decoding = "async";
  image.width = 360;
  image.height = 360;
  return image;
}

const CARD_BACK = `<defs><linearGradient id="gb" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e2cb8f"/><stop offset=".5" stop-color="#c4a766"/><stop offset="1" stop-color="#8f7a48"/></linearGradient></defs><rect x="10" y="10" width="230" height="330" rx="14" fill="none" stroke="url(#gb)" stroke-width="1.5"/><rect x="18" y="18" width="214" height="314" rx="10" fill="none" stroke="#c4a766" stroke-opacity=".35" stroke-width=".8"/><g fill="none" stroke="#c4a766" stroke-opacity=".14">${Array.from({ length: 9 }, (_, i) => `<path d="M18 ${232 + i * 11}c40-16 70 10 110-4s60-18 104 2"/>`).join("")}</g><circle cx="125" cy="150" r="56" fill="#141516" stroke="url(#gb)" stroke-width="1.4"/><path d="M92 172 116 132l10 15 9-12 24 37" fill="none" stroke="url(#gb)" stroke-width="2.4" stroke-linejoin="round"/><path d="M108 172l9-11 6 6" fill="none" stroke="#c4a766" stroke-width="1.6"/><text x="125" y="236" text-anchor="middle" font-size="22" fill="#c4a766" letter-spacing="6">露坑</text><text x="125" y="258" text-anchor="middle" font-style="italic" font-size="13" fill="#8f7a48" letter-spacing="2">Brew Experience</text>`;

const TRUCK = `<path d="M14 34c0-10 8-18 18-18h120c6 0 11 3 14 8l28 34h14c8 0 14 6 14 14v28H14z" fill="#efe6d2" stroke="#8f7a48" stroke-width="2"/><path d="M160 26l26 32h-26z" fill="#2a2d30" stroke="#8f7a48" stroke-width="1.5"/><rect x="24" y="2" width="96" height="18" rx="5" fill="#0e0f10" stroke="#c4a766" stroke-width="1.5"/><text x="72" y="16" text-anchor="middle" font-size="13" fill="#c4a766" letter-spacing="2">露坑 COFFEE</text>${Array.from({ length: 8 }, (_, i) => `<path d="M${30 + i * 13} 30h13v9a6.5 6.5 0 0 1-13 0z" fill="${i % 2 ? "#efe6d2" : "#c4a766"}"/>`).join("")}<rect x="32" y="44" width="96" height="34" rx="4" fill="#2a2d30"/><path d="M58 70h12v-10H58z M70 62h4a3 3 0 0 1 0 6h-4" fill="none" stroke="#e7e3d8" stroke-width="1.6"/><path d="M86 58l6-8 4 5 3-3 8 10" fill="none" stroke="#c4a766" stroke-width="1.6"/><text x="80" y="98" text-anchor="middle" font-size="18" font-weight="700" fill="#3a2a24" letter-spacing="4">露坑</text><rect x="138" y="46" width="22" height="50" rx="3" fill="none" stroke="#8f7a48" stroke-width="1.5"/><circle cx="214" cy="80" r="4" fill="#f6d76b"/><rect x="12" y="102" width="206" height="6" rx="3" fill="#8f7a48"/><g class="brew-wheel"><circle cx="58" cy="112" r="17" fill="#1a1b1d"/><circle cx="58" cy="112" r="7" fill="#c4a766"/><path d="M58 98v28M44 112h28" stroke="#5a5a5a" stroke-width="2"/></g><g class="brew-wheel"><circle cx="178" cy="112" r="17" fill="#1a1b1d"/><circle cx="178" cy="112" r="7" fill="#c4a766"/><path d="M178 98v28M164 112h28" stroke="#5a5a5a" stroke-width="2"/></g>`;
const WORKER = `<g class="brew-leg brew-leg-a"><path d="M17 44l-3 18" stroke="#2b2d30" stroke-width="5" stroke-linecap="round"/></g><g class="brew-leg brew-leg-b"><path d="M23 44l3 18" stroke="#2b2d30" stroke-width="5" stroke-linecap="round"/></g><path d="M10 26c0-5 4-8 10-8s10 3 10 8v20H10z" fill="#5f7b86"/><path d="M13 27h14v19H13z" fill="#efe6d2"/><path d="M10 28l-5 10M30 28l5-10" stroke="#e8c4a0" stroke-width="4" stroke-linecap="round"/><circle cx="20" cy="11" r="7.5" fill="#e8c4a0"/><path d="M12 9c1-6 15-6 16 0h4v3H12z" fill="#c4a766"/><circle cx="23" cy="12" r="1" fill="#2b1d18"/>`;

// ── 牌組小卡（首頁「手沖體驗」區）──

export function experienceMini(view: ExperienceView, onOpen: (from: HTMLElement) => void): HTMLButtonElement {
  const { experience } = view;
  const node = el("button", `brew-mini brew-theme-${experience.theme}${view.soldOut ? " brew-soldout" : ""}`);
  node.type = "button";
  node.dataset.experienceId = String(experience.id);
  node.setAttribute("aria-label", `抽 ${experience.title}${view.soldOut ? "（今日售完）" : ""}`);
  const picture = art(experience);
  if (picture) node.append(picture);
  if (experience.tag) node.append(el("span", "brew-tag", experience.tag));
  node.append(el("b", "brew-mini-title", experience.title));
  if (experience.origin) node.append(el("small", "brew-mini-origin", experience.origin));
  if (view.soldOut) node.append(el("span", "brew-mini-off", "今日售完"));
  node.addEventListener("click", () => onOpen(node));
  return node;
}

// ── 抽卡舞台 ──

interface Stage { root: HTMLDivElement; card: HTMLDivElement }

function buildCard(view: ExperienceView, priceLabel: string): HTMLDivElement {
  const { experience } = view;
  const card = el("div", "brew-card");
  const back = el("div", "brew-face brew-back");
  back.append(svg(CARD_BACK, "0 0 250 350", "brew-back-art"));
  const front = el("article", `brew-face brew-front brew-theme-${experience.theme}`);
  const picture = art(experience);
  if (picture) front.append(picture);
  if (experience.tag) front.append(el("span", "brew-tag", experience.tag));
  front.append(el("h3", "brew-title", experience.title));
  if (experience.origin) front.append(el("p", "brew-origin", experience.origin));
  if (experience.notes) front.append(el("p", "brew-notes", experience.notes));
  if (experience.description) front.append(el("p", "brew-desc", experience.description));
  const foot = el("div", "brew-foot");
  foot.append(el("span", "", "手沖體驗"), el("strong", "", priceLabel));
  front.append(foot);
  card.append(back, front);
  return card;
}

function originOf(card: HTMLElement, from: HTMLElement) {
  const a = from.getBoundingClientRect();
  const c = card.getBoundingClientRect();
  return {
    x: a.left + a.width / 2 - (c.left + c.width / 2),
    y: a.top + a.height / 2 - (c.top + c.height / 2),
    s: a.width / c.width,
  };
}
type Origin = ReturnType<typeof originOf>;

async function flip(card: HTMLElement, base: number): Promise<void> {
  await play(card, [
    { transform: `rotateY(${base}deg) scale(1)` },
    { transform: `rotateY(${base + 100}deg) scale(1.08)`, offset: .55 },
    { transform: `rotateY(${base + 180}deg) scale(1)` },
  ], { duration: 640, easing: "cubic-bezier(.3,.7,.2,1)" });
  card.querySelector(".brew-front")?.classList.add("brew-shine");
}

function speedLines(stage: HTMLElement): HTMLElement {
  const box = el("div", "brew-speed");
  for (let i = 0; i < 14; i += 1) {
    const line = el("i");
    line.style.top = `${20 + Math.random() * 60}%`;
    line.style.width = `${80 + Math.random() * 160}px`;
    box.append(line);
    line.animate([{ transform: "translateX(110vw)", opacity: 0 }, { opacity: .7, offset: .2 }, { transform: "translateX(-60vw)", opacity: 0 }],
      { duration: 420 + Math.random() * 260, delay: Math.random() * 900, iterations: 2 });
  }
  stage.append(box);
  return box;
}

function skid(stage: HTMLElement, vehicle: HTMLElement, before: HTMLElement): void {
  const r = vehicle.getBoundingClientRect();
  const reach = Math.min(window.innerWidth * .5, 260);
  const marks = el("div", "brew-skid");
  marks.style.top = `${r.bottom - 6}px`;
  marks.style.left = `${r.left - reach}px`;
  marks.style.width = `${reach + r.width * .55}px`;
  marks.append(el("span"), el("span"));
  stage.insertBefore(marks, before);
  void play(marks, [{ transform: "scaleX(0)", opacity: .9 }, { transform: "scaleX(1)", opacity: .9, offset: .35 }, { transform: "scaleX(1)", opacity: 0 }],
    { duration: 2600, easing: "ease-out" }).then(() => marks.remove());
  for (let i = 0; i < 7; i += 1) {
    const puff = el("span", "brew-puff");
    const size = 40 + Math.random() * 50;
    puff.style.width = puff.style.height = `${size}px`;
    puff.style.left = `${r.left - size * .3 + Math.random() * 30}px`;
    puff.style.top = `${r.bottom - size * .7 - Math.random() * 30}px`;
    stage.insertBefore(puff, before);
    void play(puff, [{ transform: "translate(0,0) scale(.3)", opacity: .85 },
      { transform: `translate(${-60 - Math.random() * 80}px,${-20 - Math.random() * 40}px) scale(1.6)`, opacity: 0 }],
    { duration: 900 + Math.random() * 500, delay: i * 40, easing: "cubic-bezier(.1,.6,.3,1)" }).then(() => puff.remove());
  }
}

const RUNNERS: Record<Effect, (s: Stage, o: Origin) => Promise<void>> = {
  async soar({ card }, o) {
    await play(card, [
      { transform: `translate(${o.x}px,${o.y}px) scale(${o.s}) rotateY(180deg)`, easing: "cubic-bezier(.35,0,.55,1)" },
      { transform: "translate(0,-14vh) scale(.78) rotateY(450deg) rotateZ(-8deg)", offset: .26, easing: "cubic-bezier(.3,0,.6,1)" },
      { transform: "translate(0,-76vh) scale(.5) rotateY(900deg) rotateZ(8deg)", offset: .56, easing: "ease-out" },
      { transform: "translate(0,-80vh) scale(.5) rotateY(990deg) rotateZ(10deg)", offset: .64, easing: "cubic-bezier(.55,0,.8,.4)" },
      { transform: "translate(0,9vh) scale(1.04) rotateY(1440deg) rotateZ(-3deg)", offset: .86, easing: "ease-out" },
      { transform: "translate(0,-2vh) scale(.99) rotateY(1440deg) rotateZ(1deg)", offset: .94, easing: "ease-in-out" },
      { transform: "translate(0,0) scale(1) rotateY(1440deg)" },
    ], { duration: 2600 });
    await wait(160);
    await flip(card, 1440);
  },
  async seal({ root, card }, o) {
    const rays = el("div", "brew-rays");
    root.prepend(rays);
    await play(card, [{ transform: `translate(${o.x}px,${o.y}px) scale(${o.s}) rotateY(180deg)` },
      { transform: "translate(0,0) scale(.92) rotateY(360deg)" }], { duration: 650, easing: "cubic-bezier(.2,.8,.2,1)" });
    void play(rays, [{ opacity: 0, transform: "scale(.4) rotate(0deg)" }, { opacity: 1, transform: "scale(1) rotate(40deg)", offset: .4 },
      { opacity: .55, transform: "scale(1.1) rotate(120deg)" }], { duration: 2200, easing: "ease-out" });
    await play(card, [-2, 2, -1.5, 1, 0].map((z, i) => ({ transform: `scale(${.95 + i * .015}) rotateY(360deg) rotateZ(${z}deg)` })),
      { duration: 700, easing: "ease-in-out" });
    await play(card, [{ transform: "scale(1) rotateY(360deg)" }, { transform: "scale(1.12) rotateY(720deg)", offset: .6 },
      { transform: "scale(1) rotateY(900deg)" }], { duration: 820, easing: "cubic-bezier(.3,.7,.2,1)" });
    card.querySelector(".brew-front")?.classList.add("brew-shine");
    void play(rays, [{ opacity: .55 }, { opacity: 0 }], { duration: 900 });
  },
  async shuffle({ root, card }, o) {
    // 洗牌時真正那張先藏起來（縮到看不見；不動 opacity，理由同咖啡車）。
    card.animate([{ transform: "scale(0.001)" }, { transform: "scale(0.001)" }], { fill: "forwards" });
    const ghosts = [0, 1, 2].map(() => {
      const ghost = el("div", "brew-card");
      const back = el("div", "brew-face brew-back");
      back.append(svg(CARD_BACK, "0 0 250 350", "brew-back-art"));
      ghost.append(back);
      root.append(ghost);
      return ghost;
    });
    await Promise.all(ghosts.map((g, i) => play(g, [
      { transform: `translate(${o.x}px,${o.y}px) scale(${o.s})`, opacity: 0 },
      { transform: `translate(${(i - 1) * 6}px,${(i - 1) * -4}px) scale(.82)`, opacity: 1 },
    ], { duration: 520, delay: i * 70, easing: "cubic-bezier(.2,.8,.2,1)" })));
    for (let round = 0; round < 3; round += 1) {
      await Promise.all(ghosts.map((g, i) => {
        const side = (i + round) % 3 - 1;
        return play(g, [{ transform: `translate(${(i - 1) * 6}px,0) scale(.82)` },
          { transform: `translate(${side * 92}px,${Math.abs(side) * 14}px) scale(.82) rotateZ(${side * 9}deg)`, offset: .5 },
          { transform: `translate(${(i - 1) * 6}px,0) scale(.82)` }], { duration: 340, easing: "ease-in-out" });
      }));
    }
    ghosts.slice(0, 2).forEach((g) => void play(g, [{ opacity: 1 }, { opacity: 0, transform: "translate(0,40px) scale(.75)" }], { duration: 300 }));
    await play(ghosts[2]!, [{ transform: "translate(6px,0) scale(.82)" }, { transform: "translate(0,-14vh) scale(.9) rotateZ(-6deg)", offset: .5 },
      { transform: "translate(0,0) scale(1) rotateZ(0)" }], { duration: 560, easing: "cubic-bezier(.3,.7,.2,1)" });
    card.animate([{ transform: "rotateY(360deg)" }, { transform: "rotateY(360deg)" }], { fill: "forwards" });
    ghosts.forEach((g) => g.remove());
    await flip(card, 360);
  },
  async bloom({ root, card }, o) {
    await play(card, [{ transform: `translate(${o.x}px,${o.y}px) scale(${o.s}) rotateY(180deg)` },
      { transform: "translate(0,-70vh) scale(.6) rotateY(360deg) rotateZ(-200deg)", offset: .35 },
      { transform: "translate(0,6vh) scale(1.03) rotateY(360deg) rotateZ(8deg)", offset: .8 },
      { transform: "translate(0,0) scale(1) rotateY(360deg) rotateZ(0)" }], { duration: 1150, easing: "cubic-bezier(.5,0,.3,1)" });
    for (let i = 0; i < 16; i += 1) {
      const bit = el("span", i % 3 === 2 ? "brew-bit brew-petal" : "brew-bit brew-bean");
      root.append(bit);
      const angle = (i / 16) * Math.PI * 2 + Math.random() * .3;
      const dist = 150 + Math.random() * 90;
      void play(bit, [{ transform: "translate(0,0) rotate(0) scale(.6)", opacity: 1 },
        { transform: `translate(${Math.cos(angle) * dist}px,${Math.sin(angle) * dist + 60}px) rotate(${Math.random() * 540}deg) scale(1)`, opacity: 0 }],
      { duration: 1100 + Math.random() * 400, easing: "cubic-bezier(.1,.7,.3,1)" }).then(() => bit.remove());
    }
    await flip(card, 360);
  },
  async smash({ root, card }, o) {
    const ground = Math.min(window.innerHeight * .22, 200);
    const h = card.offsetHeight;
    const floor = el("div", "brew-floor");
    floor.style.top = `calc(50% + ${ground}px)`;
    root.append(floor);
    void play(floor, [{ opacity: 0 }, { opacity: 1 }], { duration: 300 });
    const s = .62;
    const stuckY = ground - (h * s) / 2 + h * s * .26;
    await play(card, [{ transform: `translate(${o.x}px,${o.y}px) scale(${o.s}) rotateY(180deg)` },
      { transform: "translate(0,-82vh) scale(.5) rotateY(360deg) rotateZ(-180deg)", offset: .38, easing: "cubic-bezier(.6,0,1,.5)" },
      { transform: `translate(-6px,${stuckY}px) scale(${s}) rotateY(360deg) rotateZ(24deg)` }], { duration: 1150, easing: "ease-out" });
    const crack = svg(["M0 0l-18 10-10 -4-22 16-20 2-26 14", "M0 0l16 8 8 14 24 4 10 16 30 6", "M0 0l-4 18 6 12-8 20",
      "M0 0l-30 2-24 -2-30 6-40 -2", "M0 0l34 -2 26 4 30 -2 40 4", "M-28 6l-6 18-14 10", "M24 8l4 20 16 12"]
      .map((d) => `<path d="${d}" pathLength="1"/>`).join(""), "-160 -10 320 80", "brew-crack");
    crack.style.top = `calc(50% + ${ground - 10}px)`;
    root.append(crack);
    crack.querySelectorAll("path").forEach((p, i) => void play(p, [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration: 260 + i * 40, easing: "ease-out" }));
    root.animate([0, 1, 2, 3, 4, 5, 6].map((i) => ({ transform: `translate(${i % 2 ? -7 : 7}px,${i % 2 ? 5 : -4}px)` })).concat([{ transform: "translate(0,0)" }]), { duration: 380 });
    for (let i = 0; i < 12; i += 1) {
      const chip = el("span", i % 3 ? "brew-chip" : "brew-dust");
      chip.style.top = `calc(50% + ${ground - 6}px)`;
      root.append(chip);
      const dir = (i % 2 ? 1 : -1) * (20 + Math.random() * 120);
      void play(chip, [{ transform: "translate(0,0) rotate(0) scale(.6)", opacity: 1 },
        { transform: `translate(${dir * .6}px,${-40 - Math.random() * 70}px) rotate(${Math.random() * 300}deg) scale(1)`, opacity: 1, offset: .45 },
        { transform: `translate(${dir}px,${10 + Math.random() * 20}px) rotate(${Math.random() * 500}deg) scale(1.2)`, opacity: 0 }],
      { duration: 700 + Math.random() * 400, easing: "cubic-bezier(.2,.7,.4,1)" }).then(() => chip.remove());
    }
    await wait(800);
    await play(card, [{ transform: `translate(-6px,${stuckY}px) scale(${s}) rotateY(360deg) rotateZ(24deg)` },
      { transform: `translate(-2px,${stuckY + 6}px) scale(${s}) rotateY(360deg) rotateZ(20deg)`, offset: .15 },
      { transform: "translate(0,-22vh) scale(.85) rotateY(360deg) rotateZ(-8deg)", offset: .6 },
      { transform: "translate(0,0) scale(1) rotateY(360deg) rotateZ(0)" }], { duration: 900, easing: "cubic-bezier(.3,.6,.2,1)" });
    void play(floor, [{ opacity: 1 }, { opacity: 0 }], { duration: 700 }).then(() => floor.remove());
    void play(crack, [{ opacity: 1 }, { opacity: 0 }], { duration: 600 }).then(() => crack.remove());
    await flip(card, 360);
  },
  async truck({ root, card }, o) {
    const W = window.innerWidth;
    const h = card.offsetHeight;
    const ground = Math.min(window.innerHeight * .2, 190);
    const s = .36;
    const cardX = Math.min(W * .32, 150);
    const cardY = ground - (h * s) / 2;
    const road = el("div", "brew-road");
    road.style.top = `calc(50% + ${ground - 30}px)`;
    root.insertBefore(road, card);
    void play(road, [{ opacity: 0 }, { opacity: 1 }], { duration: 300 });
    await play(card, [{ transform: `translate(${o.x}px,${o.y}px) scale(${o.s}) rotateY(180deg)` },
      { transform: `translate(${cardX}px,${cardY - 120}px) scale(${s}) rotateY(360deg) rotateZ(-12deg)`, offset: .6 },
      { transform: `translate(${cardX}px,${cardY}px) scale(${s}) rotateY(360deg) rotateZ(0)` }], { duration: 700, easing: "cubic-bezier(.4,0,.3,1)" });

    const truck = el("div", "brew-truck");
    truck.append(svg(TRUCK, "0 0 230 134", "brew-truck-art"));
    truck.style.top = `calc(50% + ${ground - 134}px)`;
    root.insertBefore(truck, card);
    const tw = 230;
    const stopX = Math.max(-W / 2 + 8, cardX - tw - 70);
    const at = (x: number, extra = "") => ({ transform: `translateX(${x}px) ${extra}` });
    const wheels = (on: boolean) => truck.classList.toggle("brew-rolling", on);
    wheels(true);
    await play(truck, [at(-W / 2 - tw - 20), at(stopX + 8, "skewX(6deg)"), at(stopX, "skewX(-2deg)"), at(stopX)], { duration: 950, easing: "cubic-bezier(.2,.8,.3,1)" });
    wheels(false);

    const worker = el("div", "brew-worker");
    worker.append(svg(WORKER, "0 0 40 66", "brew-worker-art"));
    worker.style.top = `calc(50% + ${ground - 64}px)`;
    root.append(worker);
    const doorX = stopX + tw - 70;
    const pos = (x: number, y = 0, back = false) => ({ transform: `translate(${x}px,${y}px) scaleX(${back ? -1 : 1})` });
    const hopOut = (x: number) => play(worker, [{ ...pos(x, -30), opacity: 0 }, { ...pos(x, -46), opacity: 1, offset: .4 }, { ...pos(x, 0), opacity: 1 }], { duration: 380, easing: "ease-in" });
    const hopIn = (x: number) => play(worker, [{ ...pos(x, 0, true), opacity: 1 }, { ...pos(x, -40, true), opacity: 1, offset: .5 }, { ...pos(x, -24, true), opacity: 0 }], { duration: 340 });
    await hopOut(doorX);
    worker.classList.add("brew-running");
    await play(worker, [pos(doorX), pos(cardX - 22)], { duration: 420, easing: "linear" });
    worker.classList.remove("brew-running");
    const carryS = s * .62;
    const carryY = ground - 64 - (h * carryS) / 2 - 18;
    await play(card, [{ transform: `translate(${cardX}px,${cardY}px) scale(${s}) rotateY(360deg)` },
      { transform: `translate(${cardX - 8}px,${carryY}px) scale(${carryS}) rotateY(360deg) rotateZ(-6deg)` }], { duration: 260, easing: "ease-out" });
    worker.classList.add("brew-running");
    await Promise.all([
      play(worker, [pos(cardX - 22, 0, true), pos(doorX, 0, true)], { duration: 460, easing: "linear" }),
      play(card, [{ transform: `translate(${cardX - 8}px,${carryY}px) scale(${carryS}) rotateY(360deg) rotateZ(-6deg)` },
        { transform: `translate(${doorX + 14}px,${carryY + 4}px) scale(${carryS}) rotateY(360deg) rotateZ(4deg)`, offset: .5 },
        { transform: `translate(${doorX + 14}px,${carryY}px) scale(${carryS}) rotateY(360deg) rotateZ(-4deg)` }], { duration: 460, easing: "linear" }),
    ]);
    worker.classList.remove("brew-running");
    // 卡片「進車裡」用縮到看不見，不動 opacity：動了 opacity 瀏覽器會把 3D 卡片壓平，翻面後背面會透出來。
    await Promise.all([hopIn(doorX), play(card, [
      { transform: `translate(${doorX + 14}px,${carryY}px) scale(${carryS}) rotateY(360deg)` },
      { transform: `translate(${doorX + 14}px,${carryY + 20}px) scale(0.001) rotateY(360deg)` }], { duration: 300, delay: 120 })]);

    const lines = speedLines(root);
    wheels(true);
    await play(truck, [at(stopX), at(stopX - 14, "skewX(8deg)"), at(W / 2 + 40, "skewX(-18deg)")], { duration: 700, easing: "cubic-bezier(.6,0,.9,.5)" });
    await wait(240);
    const brakeX = -tw / 2;
    await play(truck, [at(-W / 2 - tw - 60, "skewX(-18deg)"), { ...at(brakeX - 50, "skewX(-14deg)"), offset: .55 },
      { ...at(brakeX + 16, "skewX(10deg) rotate(3deg)"), offset: .82 }, { ...at(brakeX, "skewX(-3deg) rotate(-1deg)"), offset: .93 }, at(brakeX)],
    { duration: 950, easing: "cubic-bezier(.15,.7,.3,1)" });
    wheels(false);
    lines.remove();
    skid(root, truck, card);

    const door2 = brakeX + tw - 70;
    await Promise.all([hopOut(door2), play(card, [
      { transform: `translate(${door2 + 14}px,${carryY - 30}px) scale(0.001) rotateY(360deg)` },
      { transform: `translate(${door2 + 14}px,${carryY}px) scale(${carryS}) rotateY(360deg)` }], { duration: 380, easing: "ease-in" })]);
    await Promise.all([
      play(worker, [pos(door2), { ...pos(door2 + 10, -10), offset: .3 }, pos(door2)], { duration: 360 }),
      play(card, [{ transform: `translate(${door2 + 14}px,${carryY}px) scale(${carryS}) rotateY(360deg)` },
        { transform: `translate(${door2 / 2}px,${carryY - 120}px) scale(.7) rotateY(360deg) rotateZ(8deg)`, offset: .55 },
        { transform: "translate(0,0) scale(1) rotateY(360deg) rotateZ(0)" }], { duration: 820, easing: "cubic-bezier(.3,.6,.2,1)" }),
    ]);
    await hopIn(door2);
    worker.remove();
    wheels(true);
    void play(truck, [at(brakeX), at(W / 2 + 60, "skewX(-14deg)")], { duration: 700, easing: "cubic-bezier(.6,0,.9,.5)" }).then(() => truck.remove());
    void play(road, [{ opacity: 1 }, { opacity: 0 }], { duration: 1200, delay: 500 }).then(() => road.remove());
    await flip(card, 360);
  },
};

export interface DrawOptions {
  /** 加入購物車：回錯誤訊息（顯示在面板上）或 null（成功，關閉舞台）。 */
  onAdd: (optionIds: number[], qty: number) => string | null;
  /** 舞台關閉後（焦點還給原本那張小卡）。 */
  onClose: () => void;
}

const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** 點小卡：抽卡動畫 → 翻開 → 「看體驗內容」→ 補選必選項 → 加入購物車。 */
export async function drawExperience(view: ExperienceView, from: HTMLElement, options: DrawOptions): Promise<void> {
  const priceLabel = view.priceFrom ? `${money(view.price)} 起` : money(view.price);
  const root = el("div", "brew-stage");
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  root.setAttribute("aria-label", `${view.experience.title}・手沖體驗`);
  const card = buildCard(view, priceLabel);
  root.append(card);
  document.body.append(root);
  document.body.classList.add("sheet-open");
  requestAnimationFrame(() => root.classList.add("brew-on"));
  from.classList.add("brew-drawn");

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    root.remove();
    document.body.classList.remove("sheet-open");
    from.classList.remove("brew-drawn");
    from.focus({ preventScroll: true });
    options.onClose();
  };
  root.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });

  const chosen = view.experience.effect === "random"
    ? EFFECTS[Math.floor(Math.random() * EFFECTS.length)]!
    : view.experience.effect;
  if (reduced()) {
    card.animate([{ transform: "rotateY(180deg)" }, { transform: "rotateY(180deg)" }], { fill: "forwards" });
  } else {
    await RUNNERS[chosen]({ root, card }, originOf(card, from));
  }
  if (closed) return;

  const closeButton = el("button", "brew-close", "×");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "關閉");
  closeButton.addEventListener("click", close);
  const peek = el("button", "brew-peek", view.soldOut ? "今日售完" : "看體驗內容");
  peek.type = "button";
  peek.disabled = view.soldOut;
  root.append(closeButton, peek);
  requestAnimationFrame(() => peek.classList.add("brew-on"));
  peek.focus({ preventScroll: true });
  peek.addEventListener("click", () => openPanel(view, card, root, peek, options, close));
}

function groupField(group: OptionGroupView): HTMLFieldSetElement {
  const field = el("fieldset", "opt-group");
  const rule = group.min_select === 1 && group.max_select === 1 ? "必選 1 項" : `選 ${group.min_select}–${group.max_select} 項`;
  field.append(el("legend", "opt-group-name", `${group.name} · ${rule}`));
  for (const option of group.options) {
    const label = el("label", "opt-choice");
    const input = el("input");
    input.type = group.max_select === 1 ? "radio" : "checkbox";
    input.name = `brew-group-${group.id}`;
    input.value = String(option.id);
    input.disabled = !option.available || option.remaining === 0;
    label.append(input, el("span", "", option.name));
    if (option.price_delta > 0) label.append(el("span", "opt-extra", `+${money(option.price_delta)}`));
    if (input.disabled) label.append(el("span", "opt-off", "售完"));
    field.append(label);
  }
  return field;
}

function openPanel(view: ExperienceView, card: HTMLElement, root: HTMLElement, peek: HTMLElement, options: DrawOptions, close: () => void): void {
  peek.remove();
  const panel = el("section", "brew-panel");
  panel.setAttribute("aria-label", "體驗內容");
  panel.append(el("h4", "brew-panel-title", `${view.experience.title}・手沖體驗`));
  if (view.experience.includes.length) {
    const list = el("ul", "brew-includes");
    for (const include of view.experience.includes) {
      const li = el("li");
      li.append(el("b", "", include.title));
      if (include.detail) li.append(el("small", "", include.detail));
      list.append(li);
    }
    panel.append(list);
  }
  const groups = el("div", "brew-groups");
  view.pending.forEach((group) => groups.append(groupField(group)));
  panel.append(groups);
  const row = el("div", "brew-row");
  const price = el("span", "brew-price", view.priceFrom ? `${money(view.price)} 起` : money(view.price));
  const add = el("button", "brew-add", "加入購物車");
  add.type = "button";
  row.append(price, add);
  const error = el("p", "field-error");
  error.setAttribute("role", "alert");
  const again = el("button", "brew-again", "換一張");
  again.type = "button";
  again.addEventListener("click", close);
  panel.append(row, error, again);
  root.append(panel);

  const deltas = new Map(view.pending.flatMap((g) => g.options.map((o) => [o.id, o.price_delta] as const)));
  groups.addEventListener("change", () => {
    const extra = Array.from(groups.querySelectorAll<HTMLInputElement>("input:checked"), (i) => deltas.get(Number(i.value)) ?? 0)
      .reduce((sum, d) => sum + d, 0);
    const complete = view.pending.every((g) => groups.querySelectorAll(`input[name="brew-group-${g.id}"]:checked`).length >= g.min_select);
    price.textContent = complete ? money(view.price + extra) : `${money(view.price + extra)} 起`;
  });
  add.addEventListener("click", () => {
    const picked = Array.from(groups.querySelectorAll<HTMLInputElement>("input:checked"), (i) => Number(i.value));
    const message = options.onAdd([...view.experience.option_ids, ...picked], 1);
    if (message === null) close();
    else error.textContent = message;
  });

  const lift = reduced() ? 1 : 420;
  const current = getComputedStyle(card).transform;
  card.animate([{ transform: current }, { transform: `${current === "none" ? "" : current} translateY(-26%) scale(.6)` }],
    { duration: lift, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" });
  panel.animate([{ transform: "translateY(105%)" }, { transform: "translateY(0)" }], { duration: lift, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" });
  add.focus({ preventScroll: true });
}
