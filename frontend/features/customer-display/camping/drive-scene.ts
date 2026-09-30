import { DEFS } from './defs';
import { camperVan, tent } from './figures';
import { pine, cloud } from './nature';
import { ledLantern, powerStation } from './props';
import { g, h, shape, inkPath, rng, INK } from './svg';
import { DURATION, ROUTE, VAN_SCALE, sampleDrive } from './drive-state';

const group = (id: string, art: string, transform = '') => g({ id, transform }, art);
const at = (x: number, y: number, scale = 1) => `translate(${x} ${y}) scale(${scale})`;
const line = (d: string, color = INK, width = 2) => h('path', { d, fill: 'none', stroke: color, 'stroke-width': width, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
const text = (word: string, x: number, y: number, size: number, color = INK) => h('text', { x, y, fill: color, 'font-size': size, 'font-family': 'Noto Sans TC, Microsoft JhengHei, sans-serif', 'font-weight': 600, 'text-anchor': 'middle', 'letter-spacing': 2 }, word);
function mountains(id: string, period: number, base: number, height: number, color: string, shade: string, seed: number) {
  const heightAt = (x: number) => {
    const a = x / period * Math.PI * 2;
    return base - height * (.35 + .4 * Math.abs(Math.sin(a * 3 + seed)) + .2 * Math.sin(a * 7 + seed * .2) + .12 * Math.cos(a * 11));
  };
  const count = 180;
  let ridge = `M0 ${heightAt(0)}`;
  for (let i = 1; i <= count; i++) ridge += ` L${period * i / count} ${heightAt(period * i / count)}`;
  const body = ridge + ` L${period} 900 L0 900 Z`;
  let texture = '';
  for (let i = 0; i < 85; i++) {
    const x = period * (i + .3) / 85, y = heightAt(x) + 15;
    texture += line(`M${x} ${y} q13 32 28 62`, shade, .8);
  }
  return `<clipPath id="${id}-clip">${h('path', { d: body })}</clipPath>` +
    group(id, h('path', { d: body, fill: color }) + g({ 'clip-path': `url(#${id}-clip)`, opacity: .42 }, texture) + line(ridge, shade, 1.5));
}
function shop() {
  let stock = '';
  for (let i = 0; i < 3; i++) {
    stock += g({ transform: at(-105 + i * 60, -42, .55) }, ledLantern());
    stock += g({ transform: at(-105 + i * 60, 23, .38) }, powerStation());
  }
  return g({ transform: at(0, 721) },
    shape('M-267 12 L260 12 L282 34 L-280 34 Z', '#bfa47d', 2, 'cs-hatch-fine'),
    shape('M-231 -220 H231 V12 H-231 Z', '#ece5cd', 2.5, 'cs-hatch-fine'),
    shape('M-260 -220 L-170 -292 H170 L260 -220 Z', '#4e6c59', 2.6, 'cs-hatch-fine'),
    inkPath('M-170 -288 H170 M-236 -235 H237', 1.2, { stroke: '#a7b298' }),
    shape('M-196 -201 H196 V-137 H-196 Z', '#eee5c9', 2.4),
    text('露坑選物', 0, -158, 38, '#345746'),
    shape('M-199 -119 H199 V-96 H-199 Z', '#718d6b', 2),
    ...Array.from({ length: 8 }, (_, i) => shape(`M${-199 + i * 50} -96 h24 v20 q-12 11 -24 0 Z`, i % 2 ? '#d6cfad' : '#e8dfc3', 1)),
    shape('M-195 -75 H45 V1 H-195 Z', '#bcd0c4', 2),
    line('M-195 -36 H45 M-65 -75 V1', '#617366', 3), stock,
    shape('M82 -78 H183 V12 H82 Z', '#4f6c5d', 2.3),
    shape('M93 -67 H172 V-22 H93 Z', '#c4d8cb', 1.6),
    line('M96 -60 L114 -60 L96 -39', '#f4f3db', 3),
    h('circle', { cx: 170, cy: -5, r: 3, fill: '#dabb77' }),
    g({ transform: at(-277, -20) }, shape('M-36 -62 H36 L46 23 H-46 Z', '#3b5b49', 2), text('OPEN', 0, -27, 15, '#f0e8cc'), line('M-23 -12 H23', '#c7c1a2', 1.5)),
    text('露營用品店', 0, 64, 23, '#365546'),
  );
}
function lake() {
  let ripples = '';
  for (let i = 0; i < 13; i++) ripples += line(`M${-235 + (i % 4) * 90} ${645 + i * 7} q60 -4 ${100 + (i % 3) * 27} 0`, i % 2 ? '#cfe3d9' : '#7aa9ae', 1.4);
  return shape('M-410 650 Q-235 572 12 619 Q213 583 404 661 Q290 745 21 744 Q-269 769 -410 650 Z', '#93bbbd', 1.8) + ripples +
    shape('M-440 752 Q-120 712 398 750 L438 788 Q-148 766 -440 790 Z', '#95a681', 1.2) +
    line('M-279 759 l-9 -43 m9 43 l8 -55 M294 746 l6 -42 m-6 42 l-9 -52', '#5d7b5f', 3);
}
function sign(label: string) {
  return line('M0 710 V789', '#706345', 7) + shape('M-99 665 H75 L105 688 L75 711 H-99 Z', '#dfcca0', 2.2, 'cs-hatch-fine') + text(label, -1, 696, 22, '#4c624c');
}
function roadside() {
  const span = ROUTE / 8;
  let art = '';
  const r = rng(214);
  for (let zone = 0; zone < 8; zone++) {
    const center = (zone + .5) * span;
    let content = '';
    if (zone === 0 || zone === 7) {
      for (let i = 0; i < 11; i++) content += pine(-400 + i * 75, 750 + r() * 30, .8 + r() * 1.0);
      if (zone === 0) content += g({ transform: 'translate(260 0)' }, sign('山林公路'));
    } else if (zone === 1) {
      content = lake() + pine(-375, 755, 1.45) + pine(346, 765, 1.2) + g({ transform: 'translate(250 0)' }, sign('湖畔慢行'));
    } else if (zone === 2) {
      content = pine(-402, 745, 1.3) + shop() + pine(399, 755, 1.55);
    } else if (zone === 3) {
      content = pine(-380, 758, 1.2) + g({ transform: at(-80, 744, .72) }, tent()) + g({ transform: at(245, 737, .48) }, tent()) + g({ transform: 'translate(-325 0)' }, sign('山間營地'));
      content += g({ transform: at(70, 747, .8) }, ledLantern());
    } else if (zone === 4) {
      content = lake() + g({ transform: 'translate(-40 -45)' }, line('M-370 735 Q0 580 370 735', '#9e8561', 17) + line('M-365 700 Q0 545 365 700', '#756a50', 6));
      for (let i = 0; i < 10; i++) { const x = -350 + i * 78, y = 623 + (x / 350) ** 2 * 74; content += line(`M${x} ${y} v36`, '#756a50', 5); }
      content += pine(390, 770, 1.3);
    } else if (zone === 5) {
      for (let i = 0; i < 5; i++) {
        const x = -330 + i * 160;
        content += line(`M${x} 755 V649`, '#746043', 6) + shape(`M${x - 55} 684 Q${x - 90} 631 ${x - 36} 610 Q${x + 6} 564 ${x + 47} 616 Q${x + 95} 654 ${x + 55} 688 Z`, '#849664', 2, 'cs-hatch-fine');
        content += h('circle', { cx: x - 26, cy: 637, r: 7, fill: '#c5a65a' }) + h('circle', { cx: x + 20, cy: 663, r: 7, fill: '#c5a65a' });
      }
    } else {
      for (let i = 0; i < 6; i++) content += line(`M${-360 + i * 140} 692 V781`, '#8c8063', 8);
      content += line('M-410 704 Q0 688 410 704 M-410 744 Q0 728 410 744', '#b09c78', 9) + g({ transform: 'translate(305 0)' }, sign('雲海展望'));
    }
    art += g({ transform: `translate(${center} 0)` }, content);
  }
  return art;
}
function foreground() {
  const r = rng(92); let out = '';
  for (let i = 0; i < 90; i++) {
    const x = i / 90 * ROUTE * 1.25, y = 964 + r() * 30;
    out += line(`M${x} ${y} l-7 -14 m7 14 l7 -20 m-7 20 l14 -6`, '#80916a', 1.7);
    if (i % 7 === 0) out += h('circle', { cx: x + 8, cy: y - 21, r: 4, fill: '#e9d7a4' });
  }
  return out;
}
function repeatStrip(id: string, asset: string, period: number) {
  return group(id, [-1, 0, 1, 2].map(i => h('use', { href: '#' + asset, x: period * i })).join(''));
}
export function buildDriveHtml() {
  const dash = ROUTE / 128;
  const defs = DEFS + `<linearGradient id="drive-sky" x2="0" y2="1"><stop stop-color="#b9d5df"/><stop offset="1" stop-color="#f1eddb"/></linearGradient>` +
    mountains('far-art', ROUTE * .25, 603, 264, '#b3c4c3', '#8babae', .4) +
    mountains('mid-art', ROUTE * .5, 749, 177, '#91a78e', '#718d77', 1.2) +
    group('roadside-art', roadside()) + group('foreground-art', foreground()) +
    group('lane-art', h('path', { d: `M0 905 H${ROUTE}`, stroke: '#f4ebce', 'stroke-width': 5, 'stroke-dasharray': `${dash * .58} ${dash * .42}` }));
  return `<svg xmlns="http://www.w3.org/2000/svg" id="drive" width="100%" height="100%" viewBox="0 0 1000 1000" role="img" aria-label="露坑露營車持續行駛，沿途經過山林、湖畔、露坑選物用品店與營地"><defs>${defs}</defs>` +
    h('rect', { x: -200, y: -1400, width: 2400, height: 2600, fill: 'url(#drive-sky)' }) +
    group('sun', h('circle', { cx: 810, cy: 236, r: 52, fill: '#efe1bb' })) +
    group('sky-clouds', g({ transform: at(195, 275, 1.1), opacity: .7 }, cloud(225)) + g({ transform: at(720, 380, .8), opacity: .5 }, cloud(245, '#f7f4e8', '#dce5df', 2))) +
    repeatStrip('far', 'far-art', ROUTE * .25) + repeatStrip('mid', 'mid-art', ROUTE * .5) +
    h('rect', { x: -100, y: 741, width: 2100, height: 300, fill: '#a5b48a' }) +
    h('path', { d: 'M-100 793 Q500 811 2000 796 V828 H-100 Z', fill: '#c9bf99' }) +
    repeatStrip('near', 'roadside-art', ROUTE) +
    h('rect', { x: -100, y: 821, width: 2100, height: 127, fill: '#9d9d87' }) +
    line('M-100 833 H2000 M-100 936 H2000', '#d8cfaa', 3) +
    repeatStrip('lane', 'lane-art', ROUTE) +
    group('van', camperVan(), at(390, 869, VAN_SCALE)) +
    h('path', { d: 'M-100 948 Q650 951 2000 945 V1400 H-100 Z', fill: '#bbc199' }) +
    repeatStrip('foreground', 'foreground-art', ROUTE * 1.25) +
    h('rect', { id: 'warmth', x: -100, y: -1400, width: 2200, height: 2900, fill: '#e4b46e', opacity: 0, 'pointer-events': 'none' }) +
    h('rect', { x: -100, y: -1400, width: 2200, height: 2900, fill: '#fff', opacity: .1, filter: 'url(#cs-paper)', 'pointer-events': 'none' }) + '</svg>';
}
export function createDriveRenderer(root: HTMLElement) {
  const svg = root.querySelector<SVGSVGElement>('#drive');
  if (!svg) throw Error('Missing driving scene');
  const nodes = Object.fromEntries(['far', 'mid', 'near', 'lane', 'foreground', 'sun', 'sky-clouds', 'warmth'].map(id => [id, root.querySelector('#' + id)!]));
  const wheels = Array.from(root.querySelectorAll('.cs-wheel'));
  const body = root.querySelector('.cs-van-body')!;
  return (seconds: number) => {
    const s = sampleDrive(seconds);
    for (const id of ['far', 'mid', 'near', 'foreground'] as const) nodes[id].setAttribute('transform', `translate(${-s[id]} 0)`);
    nodes.lane.setAttribute('transform', `translate(${-s.near} 0)`);
    for (const wheel of wheels) wheel.setAttribute('transform', `rotate(${s.wheel})`);
    body.setAttribute('transform', `translate(0 ${s.bounce}) rotate(${s.roll} 0 -90)`);
    nodes.sun.setAttribute('transform', `translate(${s.cloud} ${s.light * 30})`);
    nodes['sky-clouds'].setAttribute('transform', `translate(${s.cloud} 0)`);
    nodes.warmth.setAttribute('opacity', String(s.light * .09));
    root.dataset.time = s.time.toFixed(3);
  };
}
export type DriveMode = 'idle' | 'cart' | 'paid' | 'celebrate' | 'hidden';
export function createDriveController(root: HTMLElement, reduced = false, initialMode: DriveMode = 'idle') {
  const render = createDriveRenderer(root);
  let time = 0, frame = 0, last = 0, paused = false, mode = initialMode;
  const stop = () => { cancelAnimationFrame(frame); frame = 0; last = 0; };
  const paint = () => { root.dataset.mode = mode; root.style.visibility = mode === 'hidden' ? 'hidden' : ''; render(time); };
  const tick = (now: number) => {
    if (last) time = (time + Math.min((now - last) / 1000, .1)) % DURATION;
    last = now; paint(); frame = requestAnimationFrame(tick);
  };
  const sync = () => { stop(); paint(); if (!paused && !reduced && mode !== 'hidden' && !document.hidden) frame = requestAnimationFrame(tick); };
  const fit = () => {
    const box = root.getBoundingClientRect();
    const ratio = (box.width || 1000) / (box.height || 1000);
    const width = Math.max(1000, Math.min(1800, ratio * 1000));
    const height = ratio < 1 ? Math.min(2200, 1000 / ratio) : 1000;
    root.querySelector('#drive')!.setAttribute('viewBox', `0 ${1000 - height} ${width} ${height}`);
    root.querySelector('#van')!.setAttribute('transform', at(width * .39, 869, VAN_SCALE));
  };
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(fit) : null;
  observer?.observe(root);
  window.addEventListener('resize', fit);
  document.addEventListener('visibilitychange', sync);
  fit(); sync();
  return {
    setMode(next: DriveMode) { mode = next; sync(); },
    bump() { /* Keep driving; transaction feedback remains in the existing kiosk UI. */ },
    seek(seconds: number) { time = ((seconds % DURATION) + DURATION) % DURATION; paint(); },
    pause() { paused = true; stop(); },
    play() { paused = false; sync(); },
    snapshot() { return { time, mode, paused }; },
    destroy() { stop(); observer?.disconnect(); window.removeEventListener('resize', fit); document.removeEventListener('visibilitychange', sync); },
  };
}
export type DriveController = ReturnType<typeof createDriveController>;
