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
<filter id="cs-rough" x="-5%" y="-5%" width="110%" height="110%">
  <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="1" result="t"/>
  <feDisplacementMap in="SourceGraphic" in2="t" scale="3.2" xChannelSelector="R" yChannelSelector="G"/>
</filter>
<filter id="cs-pencil" x="-5%" y="-5%" width="110%" height="110%">
  <feTurbulence type="fractalNoise" baseFrequency="0.6" numOctaves="2" seed="5" result="n"/>
  <feDisplacementMap in="SourceGraphic" in2="n" scale="4" xChannelSelector="R" yChannelSelector="G" result="d"/>
  <feTurbulence type="fractalNoise" baseFrequency="1.4" numOctaves="1" seed="9" result="g"/>
  <feColorMatrix in="g" type="matrix" values="0.3 0 0 0 0.76  0.3 0 0 0 0.76  0.3 0 0 0 0.76  0 0 0 0 1" result="gm"/>
  <feBlend in="d" in2="gm" mode="multiply" result="b"/>
  <feComposite in="b" in2="d" operator="in"/>
</filter>
<pattern id="cs-hatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(-35)">
  <line x1="0" y1="0" x2="0" y2="10" stroke="${INK}" stroke-width="1.4" opacity="0.5"/>
</pattern>
<pattern id="cs-hatch-fine" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(-35)">
  <line x1="0" y1="0" x2="0" y2="7" stroke="${INK}" stroke-width="1" opacity="0.38"/>
</pattern>
<pattern id="cs-cross" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(-30)">
  <line x1="0" y1="0" x2="0" y2="9" stroke="${INK}" stroke-width="1.2" opacity="0.55"/>
  <line x1="0" y1="0" x2="9" y2="0" stroke="${INK}" stroke-width="1.2" opacity="0.35"/>
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
<radialGradient id="cs-firefly">
  <stop offset="0" stop-color="#fff7b0" stop-opacity="1"/>
  <stop offset="1" stop-color="#e8f06a" stop-opacity="0"/>
</radialGradient>
<linearGradient id="cs-beam" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#fff2c8" stop-opacity="0.55"/>
  <stop offset="1" stop-color="#fff2c8" stop-opacity="0"/>
</linearGradient>
`;
