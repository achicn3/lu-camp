// 全場共用的濾鏡、紋理與天色漸層。HTML 文件裡 url(#id) 跨 <svg> 也找得到，
// 所以只放一份在隱藏的 <svg> 裡，各圖層直接引用。id 一律 cs- 開頭避免撞名。
import { INK } from "./svg";

export const SKY_STOPS = {
  day: ["#9fcbe3", "#eef3e8"],
  golden: ["#e9b98a", "#fbe3b8"],
  sunset: ["#7c6aa0", "#f3a676"],
  night: ["#0d1a38", "#2b3f6b"],
  dawn: ["#f6d9b8", "#fdf3e2"],
} as const;

export const DEFS = `
<filter id="cs-paper" x="0" y="0" width="100%" height="100%">
  <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="3" seed="3" result="noise"/>
  <feColorMatrix in="noise" type="matrix" values="0 0 0 0 0.42  0 0 0 0 0.35  0 0 0 0 0.25  0 0 0 0.08 0" result="grain"/>
  <feTurbulence type="fractalNoise" baseFrequency="0.012 0.08" numOctaves="2" seed="8" result="fiber"/>
  <feColorMatrix in="fiber" type="matrix" values="0 0 0 0 0.55  0 0 0 0 0.45  0 0 0 0 0.3  0 0 0 0.05 0" result="fibers"/>
  <feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="fibers"/><feMergeNode in="grain"/></feMerge>
</filter>
<filter id="cs-pencil-soft" x="-2%" y="-2%" width="104%" height="104%">
  <feTurbulence type="fractalNoise" baseFrequency="1.3" numOctaves="1" seed="9" result="g"/>
  <feColorMatrix in="g" type="matrix" values="0.18 0 0 0 0.86  0.18 0 0 0 0.86  0.18 0 0 0 0.86  0 0 0 0 1" result="gm"/>
  <feTurbulence type="fractalNoise" baseFrequency="0.01 0.02" numOctaves="2" seed="44" result="p"/>
  <feColorMatrix in="p" type="matrix" values="0.14 0 0 0 0.88  0.14 0 0 0 0.88  0.14 0 0 0 0.88  0 0 0 0 1" result="pm"/>
  <feBlend in="SourceGraphic" in2="gm" mode="multiply" result="b1"/>
  <feBlend in="b1" in2="pm" mode="multiply" result="b2"/>
  <feComponentTransfer in="b2" result="b3"><feFuncR type="linear" slope="1.1"/><feFuncG type="linear" slope="1.1"/><feFuncB type="linear" slope="1.1"/></feComponentTransfer>
  <feComposite in="b3" in2="SourceGraphic" operator="in"/>
</filter>
<filter id="cs-grain" x="0" y="0" width="100%" height="100%">
  <feTurbulence type="fractalNoise" baseFrequency="1.3" numOctaves="1" seed="9" result="g"/>
  <feColorMatrix in="g" type="matrix" values="0.2 0 0 0 0.84  0.2 0 0 0 0.84  0.2 0 0 0 0.84  0 0 0 0 1" result="gm"/>
  <feBlend in="SourceGraphic" in2="gm" mode="multiply" result="b"/>
  <feComponentTransfer in="b" result="c"><feFuncR type="linear" slope="1.1"/><feFuncG type="linear" slope="1.1"/><feFuncB type="linear" slope="1.1"/></feComponentTransfer>
  <feComposite in="c" in2="SourceGraphic" operator="in"/>
</filter>
<filter id="cs-pencil-line" x="-5%" y="-40%" width="110%" height="180%">
  <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="17" result="n"/>
  <feDisplacementMap in="SourceGraphic" in2="n" scale="1.6" xChannelSelector="R" yChannelSelector="G" result="d"/>
  <feTurbulence type="fractalNoise" baseFrequency="1.8" numOctaves="1" seed="4" result="g"/>
  <feColorMatrix in="g" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -1.4 1.25" result="ga"/>
  <feComposite in="d" in2="ga" operator="in"/>
</filter>
<filter id="cs-rough" x="-5%" y="-5%" width="110%" height="110%">
  <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="1" result="t"/>
  <feDisplacementMap in="SourceGraphic" in2="t" scale="3.2" xChannelSelector="R" yChannelSelector="G"/>
</filter>
<filter id="cs-pencil" x="-5%" y="-5%" width="110%" height="110%">
  <feTurbulence type="fractalNoise" baseFrequency="0.6" numOctaves="2" seed="5" result="n"/>
  <feDisplacementMap in="SourceGraphic" in2="n" scale="3" xChannelSelector="R" yChannelSelector="G" result="d"/>
  <feTurbulence type="fractalNoise" baseFrequency="1.3" numOctaves="1" seed="9" result="g"/>
  <feColorMatrix in="g" type="matrix" values="0.3 0 0 0 0.76  0.3 0 0 0 0.76  0.3 0 0 0 0.76  0 0 0 0 1" result="gm"/>
  <feTurbulence type="fractalNoise" baseFrequency="0.018 0.03" numOctaves="3" seed="21" result="p"/>
  <feColorMatrix in="p" type="matrix" values="0.4 0 0 0 0.68  0.4 0 0 0 0.68  0.4 0 0 0 0.68  0 0 0 0 1" result="pm"/>
  <feTurbulence type="fractalNoise" baseFrequency="0.9 0.05" numOctaves="1" seed="33" result="s"/>
  <feColorMatrix in="s" type="matrix" values="0.14 0 0 0 0.9  0.14 0 0 0 0.9  0.14 0 0 0 0.9  0 0 0 0 1" result="sm"/>
  <feBlend in="d" in2="gm" mode="multiply" result="b1"/>
  <feBlend in="b1" in2="pm" mode="multiply" result="b2"/>
  <feBlend in="b2" in2="sm" mode="multiply" result="b3"/>
  <feComponentTransfer in="b3" result="b4"><feFuncR type="linear" slope="1.2"/><feFuncG type="linear" slope="1.2"/><feFuncB type="linear" slope="1.2"/></feComponentTransfer>
  <feComposite in="b4" in2="d" operator="in"/>
</filter>
<filter id="cs-wash" x="0" y="0" width="100%" height="100%">
  <feTurbulence type="fractalNoise" baseFrequency="0.004 0.012" numOctaves="3" seed="61" result="w"/>
  <feColorMatrix in="w" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.9 -0.32"/>
</filter>
<pattern id="cs-hatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(-35)">
  <line x1="0" y1="0" x2="0" y2="10" stroke="${INK}" stroke-width="1.1" opacity="0.42"/>
</pattern>
<pattern id="cs-hatch-fine" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(-35)">
  <line x1="0" y1="0" x2="0" y2="7" stroke="${INK}" stroke-width="1" opacity="0.38"/>
</pattern>
<pattern id="cs-cross" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(-30)">
  <line x1="0" y1="0" x2="0" y2="9" stroke="${INK}" stroke-width="1.2" opacity="0.55"/>
  <line x1="0" y1="0" x2="9" y2="0" stroke="${INK}" stroke-width="1.2" opacity="0.35"/>
</pattern>
<pattern id="cs-fabric" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(28)">
  <line x1="0" y1="0" x2="0" y2="6" stroke="#8a5a1c" stroke-width="0.6" opacity="0.22"/>
</pattern>
<pattern id="cs-fabric-side" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(-30)">
  <line x1="0" y1="0" x2="0" y2="6" stroke="#5c3a10" stroke-width="0.6" opacity="0.25"/>
</pattern>
<pattern id="cs-dots" width="12" height="12" patternUnits="userSpaceOnUse">
  <circle cx="3" cy="3" r="1.8" fill="#fff" opacity="0.55"/>
</pattern>
<linearGradient id="cs-sky" x1="0" y1="0" x2="0" y2="1">
  <stop id="cs-sky-top" offset="0" stop-color="${SKY_STOPS.day[0]}"/>
  <stop id="cs-sky-bottom" offset="0.75" stop-color="${SKY_STOPS.day[1]}"/>
</linearGradient>
<radialGradient id="cs-glow">
  <stop offset="0" stop-color="#ffd98a" stop-opacity="0.85"/>
  <stop offset="0.45" stop-color="#ffb85c" stop-opacity="0.35"/>
  <stop offset="1" stop-color="#ff9a3c" stop-opacity="0"/>
</radialGradient>
<radialGradient id="cs-ambient">
  <stop offset="0" stop-color="#ffb35c" stop-opacity="0.55"/>
  <stop offset="0.5" stop-color="#ffb35c" stop-opacity="0.2"/>
  <stop offset="1" stop-color="#ffb35c" stop-opacity="0"/>
</radialGradient>
<radialGradient id="cs-firefly">
  <stop offset="0" stop-color="#fff7b0" stop-opacity="1"/>
  <stop offset="1" stop-color="#e8f06a" stop-opacity="0"/>
</radialGradient>
<linearGradient id="cs-haze" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#eef2ee" stop-opacity="0"/>
  <stop offset="0.7" stop-color="#eef2ee" stop-opacity="0.55"/>
  <stop offset="1" stop-color="#eef2ee" stop-opacity="0.2"/>
</linearGradient>
<linearGradient id="cs-beam" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#fff2c8" stop-opacity="0.55"/>
  <stop offset="1" stop-color="#fff2c8" stop-opacity="0"/>
</linearGradient>
`;
