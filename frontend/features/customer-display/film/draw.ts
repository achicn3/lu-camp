import type { FilmSnapshot } from "./state";

const C = { paper: "#f4eedf", ink: "#29362e", moss: "#71855a", dark: "#344b3e", lime: "#b7c88b", gold: "#e6a53d", orange: "#b96540", night: "#263647", blue: "#8daaa7" };
const clamp = (v: number) => Math.max(0, Math.min(1, v));
const ease = (v: number) => { const x = clamp(v); return x * x * (3 - 2 * x); };
const mix = (a: number, b: number, t: number) => a + (b - a) * t;
function random(seed: number) { let n = seed; return () => { n = (Math.imul(n, 1664525) + 1013904223) >>> 0; return n / 4294967296; }; }

/** 紙、排線與道具先畫入快取；每幀只重畫小角色、光線與鏡頭。 */
export class FilmPainter {
  private assets = new Map<string, HTMLCanvasElement>();
  constructor(private ctx: CanvasRenderingContext2D) {}
  private group(x: number, y: number, scale: number, angle: number, fn: () => void) {
    this.ctx.save(); this.ctx.translate(x, y); this.ctx.rotate(angle); this.ctx.scale(scale, scale); fn(); this.ctx.restore();
  }
  private line(d: string, color = C.ink, width = 2) {
    this.ctx.strokeStyle = color; this.ctx.lineWidth = width; this.ctx.lineCap = "round"; this.ctx.lineJoin = "round"; this.ctx.stroke(new Path2D(d));
  }
  private shape(d: string, fill: string, hatch = false, bounds = [-300, -300, 600, 600]) {
    const c = this.ctx, path = new Path2D(d);
    c.fillStyle = fill; c.fill(path);
    if (hatch) {
      c.save(); c.clip(path); c.globalAlpha *= 0.22;
      const [x, y, w, h] = bounds;
      c.beginPath();
      for (let v = x - h; v < x + w + h; v += 5) { c.moveTo(v, y + h); c.lineTo(v + h * 0.62, y); }
      c.strokeStyle = C.ink; c.lineWidth = 0.65; c.stroke();
      const r = random(Math.round(x * 13 + y * 7 + w));
      c.globalAlpha *= .7;
      for(let i=0;i<Math.min(900,w*h/65);i++){
        c.fillStyle=i%3?C.ink:"#fff9e9";
        const px=x+r()*w,py=y+r()*h;
        c.fillRect(px,py,.4+r()*1.2,.3+r()*.8);
      }
      c.globalAlpha *= .65;
      c.beginPath();
      for(let v=x+w*.55;v<x+w+h;v+=4){c.moveTo(v,y+h);c.lineTo(v-h*.8,y);}
      c.stroke(); c.restore();
    }
    this.line(d, C.ink, 1.4);
    c.save(); c.translate(1, -0.7); c.globalAlpha *= 0.28; this.line(d, C.ink, 0.65); c.restore();
  }
  private ellipse(x: number, y: number, rx: number, ry: number, color: string, stroke = false) {
    const c = this.ctx; c.beginPath(); c.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2); c.fillStyle = color; c.fill();
    if (stroke) { c.strokeStyle = C.ink; c.lineWidth = 1.8; c.stroke(); }
  }
  private cached(key: string, width: number, height: number, draw: () => void, x = 0, y = 0) {
    let asset = this.assets.get(key);
    if (!asset) {
      asset = document.createElement("canvas"); asset.width = width * 2; asset.height = height * 2;
      const original = this.ctx;
      this.ctx = asset.getContext("2d")!; this.ctx.scale(2, 2); draw(); this.ctx = original;
      this.assets.set(key, asset);
    }
    this.ctx.drawImage(asset, x, y, width, height);
  }
  private paper() {
    this.cached("paper", 960, 960, () => {
      const c = this.ctx, r = random(71); c.fillStyle = C.paper; c.fillRect(0, 0, 960, 960);
      for (let i = 0; i < 25000; i++) {
        c.fillStyle = i % 3 === 0 ? "#7b715311" : "#ffffff65";
        c.fillRect(r() * 960, r() * 960, r() * 1.8 + 0.3, r() * 1.1 + 0.3);
      }
      c.strokeStyle = "#a5977330"; c.lineWidth = 0.4;
      for (let i = 0; i < 450; i++) { const x = r() * 960, y = r() * 960; c.beginPath(); c.moveTo(x, y); c.lineTo(x + r() * 9, y + 1); c.stroke(); }
    });
  }
  private rays(x: number, y: number, radius: number, alpha: number, time: number) {
    const c = this.ctx; c.save(); c.globalAlpha *= alpha;
    const g = c.createRadialGradient(x, y, 8, x, y, radius); g.addColorStop(0, "#f8cc6e99"); g.addColorStop(1, "#f8cc6e00");
    c.fillStyle = g; c.fillRect(x - radius, y - radius, radius * 2, radius * 2);
    for (let i = 0; i < 18; i++) {
      const a = i / 18 * Math.PI * 2 + time * 0.016, inner = radius * 0.75, outer = radius * (0.91 + 0.04 * Math.sin(i + time));
      this.line(`M${x + Math.cos(a) * inner} ${y + Math.sin(a) * inner} L${x + Math.cos(a) * outer} ${y + Math.sin(a) * outer}`, "#c89140", 1.1);
    }
    c.restore();
  }
  private leaf(x: number, y: number, scale: number, angle: number) {
    this.group(x, y, scale, angle, () => {
      this.shape("M0 0 C-48 -18 -58 -77 -12 -108 C28 -80 52 -28 0 0 Z", C.moss, true, [-60, -110, 120, 110]);
      this.line("M0 7 Q-12 -45 -12 -104 M-9 -65 L-31 -79 M-8 -43 L20 -63 M-5 -24 L-30 -46", "#d9dfa7", 1.2);
    });
  }
  private tree(x: number, y: number, s: number) {
    this.group(x, y, s, 0, () => {
      this.line("M0 10 L-2 -182", C.dark, 3);
      for (let i = 0; i < 5; i++) {
        const y0 = -190 + i * 32, w = 17 + i * 11;
        this.shape(`M-2 ${y0} Q${-w / 2} ${y0 + 20} ${-w} ${y0 + 44} L-10 ${y0 + 34} L-3 ${y0 + 49} L15 ${y0 + 34} L${w} ${y0 + 40} Q${w / 2} ${y0 + 15} -2 ${y0} Z`, i % 2 ? C.moss : C.dark, true, [-80, -200, 160, 240]);
      }
    });
  }
  /** 霧面充電營燈：玻璃、內柱、籠架和金屬底座各有不同紋理。 */
  private lantern(x: number, y: number, scale: number, light: number) {
    this.group(x, y, scale, 0, () => {
      this.rays(0, -128, 230, light * 0.72, 0);
      this.cached("lantern", 340, 484, () => {
        this.group(170, 440, 1, 0, () => {
          this.ellipse(0, 4, 116, 13, "#29362e18");
          this.line("M-58 -272 L-66 -336 C-80 -424 80 -424 66 -336 L58 -272", C.ink, 9);
          this.line("M-58 -272 L-66 -336 C-80 -424 80 -424 66 -336 L58 -272", "#8c9b84", 5);
          this.shape("M-74 -270 Q0 -286 74 -270 L92 -238 Q0 -218 -92 -238 Z", C.dark, true, [-100, -290, 200, 80]);
          this.shape("M-66 -232 L66 -232 L77 -49 Q0 -32 -77 -49 Z", "#e1dfc2", true, [-90, -250, 180, 220]);
          this.shape("M-31 -228 L31 -228 L36 -53 L-36 -53 Z", "#faf1d6");
          for (let i = -22; i < 30; i += 8) this.line(`M${i} -219 L${i + 3} -60`, "#d6ba72", 0.7);
          this.line("M-55 -225 L-63 -50 M55 -225 L63 -50", C.dark, 8);
          this.line("M-51 -225 L-59 -50 M59 -225 L67 -50", "#b6c3a0", 1.3);
          this.shape("M-82 -48 Q0 -29 82 -48 L89 -15 Q0 8 -89 -15 Z", C.dark, true, [-90, -50, 180, 60]);
          this.shape("M-56 -33 L56 -33 L56 -15 L-56 -15 Z", "#c0b583");
          this.ellipse(81, -42, 10, 12, C.orange, true);
          const c = this.ctx; c.font = "600 12px serif"; c.textAlign = "center"; c.fillStyle = C.dark; c.fillText("露 坑  /  LU CAMP", 0, -20);
          this.line("M-82 -252 Q0 -264 82 -252 M-80 -8 Q0 9 80 -8", "#99ac86", 1.5);
          for(let i=-70;i<=70;i+=9){this.line(`M${i} -255 l-3 16`,"#172b24",.6);this.ellipse(i,-4,1.2,.8,"#c8d0b1");}
          for(const x of [-68,68]){this.ellipse(x,-251,3.2,3.2,"#9baf95",true);this.line(`M${x-1.5} -252 l3 2`,C.ink,.7);}
          this.line("M-70 -29 l10 0 M61 -29 l10 0 M-17 -281 h34 M-21 -279 h42",C.paper,.8);
          this.line("M-39 -214 L-46 -92 M39 -193 L45 -61", "#ffffffbb", 3);
        });
      }, -170, -440);
      this.ctx.save(); this.ctx.globalCompositeOperation = "screen"; this.ctx.globalAlpha *= light * 0.8;
      this.ellipse(0, -136, 42, 86, "#ffcf64"); this.ctx.restore();
    });
  }
  /** 螢火蟲有獨立頭、胸腹、觸角與六足；翅膀動作停下後才落腳。 */
  private firefly(x: number, y: number, scale: number, time: number, flying = true, angle = 0, glow = 0.8) {
    this.group(x, y, scale, angle, () => {
      const c = this.ctx, flutter = Math.sin(time * 38);
      if (flying) {
        c.save(); c.globalAlpha *= 0.55;
        this.group(-18, -6, 1, -0.6 + flutter * 0.3, () => { this.ellipse(-12, -24, 14, 36, "#e0e6c6", true); this.line("M-7 0 Q-8 -32 -21 -48 M-8 -20 L-25 -24", "#708475", 0.8); });
        this.group(-13, -7, 1, 0.8 - flutter * 0.35, () => { this.ellipse(-12, -24, 12, 33, "#f5f2d7", true); });
        c.restore();
      }
      this.rays(-31, 6, 48, glow * 0.7, time);
      for (let i = 0; i < 3; i++) {
        const a = flying ? Math.sin(time * 8 + i) * 4 : Math.sin(time * 2 + i);
        this.line(`M${-17 + i * 8} 8 L${-23 + i * 12} ${22 + a} L${-12 + i * 12} ${flying ? 26 + a : 31}`, C.ink, 1.6);
      }
      this.ellipse(-28, 5, 22, 15, C.gold, true);
      for(let i=0;i<8;i++)this.line(`M${-46+i*4} 5 q-4 6 1 10`,"#987831",.65);
      this.shape("M-47 -1 Q-28 -20 -12 -5 L-12 9 Q-32 4 -47 -1 Z", C.dark, true, [-50, -20, 45, 35]);
      this.line("M-39 10 L-36 17 M-29 10 L-27 19 M-20 11 L-19 16", "#927439", 1);
      this.ellipse(-4, 0, 13, 12, "#674c35", true); this.ellipse(16, -5, 13, 12, "#514b36", true);
      this.ellipse(22, -9, 4.6, 5.3, C.paper, true); this.ellipse(24, -9, 2, 3, C.ink);
      this.line(`M14 -16 Q10 -34 ${9 + Math.sin(time * 2) * 4} -40 M23 -16 Q35 -29 ${37 + Math.cos(time * 2) * 4} -29`, C.ink, 1.4);
      this.ellipse(9 + Math.sin(time * 2) * 4, -40, 2.2, 2.2, C.orange);
      this.line("M23 2 q5 3 8 -1", C.ink, 1);
    });
  }
  private camp() {
    this.cached("camp", 960, 960, () => {
      const c = this.ctx;
      this.shape("M42 714 Q180 668 335 697 Q556 678 913 703 L939 801 Q478 877 21 802 Z", "#c6cb9f", true, [0, 690, 960, 155]);
      this.shape("M130 514 L174 443 L193 432 L214 391 L233 380 L254 341 L280 305 L298 341 L318 350 L335 387 L367 429 L384 412 L400 376 L430 353 L451 379 L465 379 L490 424 L527 464 L539 465 L608 561 Z", "#c7cfb5", true, [120, 300, 500, 270]);
      this.shape("M442 570 L494 496 L523 486 L554 424 L570 420 L594 384 L610 361 L628 392 L649 401 L671 440 L693 454 L725 432 L740 434 L758 417 L786 452 L808 459 L832 491 L850 500 L910 579 Z", "#aebfab", true, [430, 360, 490, 230]);
      this.shape("M249 351 L280 305 L317 360 L292 351 L277 366 L265 350 Z", C.paper);
      for (const [x,y,s] of [[126,707,1.25],[211,672,.85],[805,690,1.18],[879,703,.9],[714,648,.6]]) this.tree(x,y,s);
      // 氣柱帳：大弧形而非縮放的三角形，門襟、氣柱接縫與透氣窗。
      this.ellipse(355, 735, 186, 19, "#39463826");
      this.shape("M191 725 C191 528 277 501 351 515 C464 504 511 590 522 728 Z", "#cfaf70", true, [180, 490, 360, 260]);
      this.line("M220 727 C226 552 284 512 351 519 C427 531 446 613 450 728", C.dark, 10);
      this.line("M223 721 C229 552 284 514 351 522 C421 531 444 613 447 721", "#8ba181", 2);
      this.shape("M278 724 L278 615 Q324 546 370 615 L374 725 Z", C.dark, true, [275, 560, 100, 170]);
      this.shape("M278 615 Q301 595 324 581 L312 725 L278 724 Z", "#dfc48a");
      this.line("M289 609 L287 712 M302 605 L297 715 M468 638 l26 0 l8 32 l-28 0 Z", "#f1dbac", 1);
      // 輕量桌、摺疊椅、鈦杯、手沖壺。
      this.line("M579 714 L696 796 M696 714 L579 796", C.dark, 5);
      this.shape("M555 696 L708 696 L725 715 L548 715 Z", "#bca67b", true, [540,690,190,30]);
      for (let i=0;i<6;i++) this.line(`M${562+i*25} 698 l3 14`, "#6e6850", .7);
      this.shape("M609 651 Q590 645 595 686 L629 686 Q637 660 609 651 Z", "#a0aaa0", true, [580,640,70,60]);
      this.line("M627 660 L648 639 L655 640 M601 652 Q596 634 591 642 Q580 654 597 674", C.ink, 2);
      this.shape("M669 671 h24 v20 h-24 Z", C.paper); this.line("M693 674 q15 0 0 13", C.ink, 2);
      this.line("M507 713 L556 791 M552 713 L505 791", C.dark, 4);
      this.shape("M482 641 L545 648 L551 732 L502 731 Z", C.moss, true, [480,630,80,110]);
      this.shape("M499 731 L553 731 L568 747 L512 752 Z", C.dark);
      // 保冷箱與行動電源，幾何輪廓乾淨，扣具與插孔要讀得出來。
      this.shape("M746 739 L830 739 L826 795 L750 795 Z", "#6f8a78", true, [740,730,100,80]);
      this.shape("M740 726 L835 726 L835 741 L740 741 Z", "#e6dfc4");
      this.shape("M757 734 h8 v17 h-8 Z M811 734 h8 v17 h-8 Z", C.dark);
      this.shape("M640 760 h65 v43 h-65 Z", "#485d53", true, [630,750,90,70]);
      this.line("M657 760 v-12 h32 v12", C.dark, 4);
      this.shape("M650 771 h21 v12 h-21 Z", "#b9c797"); this.ellipse(689,779,6,6,C.paper,true);
      this.line("M697 792 Q739 824 751 801 L787 746", C.dark, 1.7);
      // 地表筆觸與小白花。
      const r = random(12); c.strokeStyle = C.moss; c.lineWidth = 1;
      for(let i=0;i<120;i++){const x=45+r()*850,y=775+r()*60;c.beginPath();c.moveTo(x,y);c.lineTo(x-2,y-5-r()*6);c.moveTo(x,y);c.lineTo(x+4,y-7);c.stroke();}
      for(const [x,y] of [[100,790],[161,817],[861,805]]){this.line(`M${x} ${y} v-18`,C.dark,1);for(let j=0;j<5;j++){const a=j/5*Math.PI*2;this.ellipse(x+Math.cos(a)*4,y-18+Math.sin(a)*4,3,3,C.paper);}this.ellipse(x,y-18,2,2,C.gold);}
    });
  }
  private star(x: number, y: number, r: number, alpha = 1) {
    this.ctx.save(); this.ctx.globalAlpha *= alpha;
    this.shape(`M${x} ${y-r} Q${x+2} ${y-2} ${x+r} ${y} Q${x+2} ${y+2} ${x} ${y+r} Q${x-2} ${y+2} ${x-r} ${y} Q${x-2} ${y-2} ${x} ${y-r} Z`, C.gold); this.ctx.restore();
  }
  private weather(time: number) {
    const c=this.ctx, rain=ease((time-21)/2)*(1-ease((time-27)/2));
    if(rain>0){c.save();c.globalAlpha=rain*.32;for(let i=0;i<70;i++){const x=(i*137+time*62)%960,y=(i*79+time*240)%870;this.line(`M${x} ${y} l-9 28`,C.blue,1.1);}c.restore();}
    const rainbow=ease((time-28)/2)*(1-ease((time-34)/2));
    if(rainbow>0){c.save();c.globalAlpha=rainbow*.42;["#bd6647","#d39a46","#c8b66c","#819b78","#749caa"].forEach((color,i)=>{c.strokeStyle=color;c.lineWidth=8;c.beginPath();c.arc(480,588,264-i*9,Math.PI,Math.PI*2);c.stroke();});c.restore();}
  }
  private macro(time: number) {
    const c=this.ctx, on=ease((time-7.8)/1.2);
    this.cached("macro-ground",960,960,()=>{
      this.shape("M84 770 Q265 739 451 753 Q677 726 902 763 L875 820 Q485 848 76 814 Z","#ddd5b9",true,[70,725,850,130]);
      this.leaf(164,775,1.7,-.6);this.leaf(808,782,1.05,.6);
      this.line("M134 810 Q168 705 152 578",C.moss,2);
      this.leaf(155,663,.48,-1);this.leaf(162,729,.6,.5);
    });
    this.lantern(540,780,1.24,on);
    this.ctx.save();this.ctx.globalAlpha=.2;
    for(let i=0;i<13;i++)this.line(`M${94+i*53} 807 q34 -12 78 -8`,"#776849",.6);
    this.ctx.restore();
    let x:number,y:number,angle:number;
    if(time<5){const p=ease(time/5);x=mix(130,610,p);y=500-Math.sin(p*Math.PI)*115;angle=Math.sin(p*Math.PI*2)*.2;}
    else if(time<8){const p=ease((time-5)/3);x=mix(610,622,p);y=mix(500,692,p);angle=.1;}
    else {const p=ease((time-8)/4);x=mix(622,460,p);y=mix(692,400,p);angle=-.3;}
    this.firefly(x,y,1.2,time,time<7.2||time>8.3,angle,on*.6+.25);
    if(time>7.7&&time<9.2){c.save();c.globalAlpha=1-clamp((time-7.7)/1.5);this.rays(640,725,70,1,time);c.restore();}
  }
  private landscape(time: number) {
    const c=this.ctx, night=ease((time-33)/5);
    this.camp(); this.weather(time);
    if(night>0){c.save();c.globalCompositeOperation="multiply";c.globalAlpha=night*.82;c.fillStyle=C.night;c.fillRect(0,0,960,960);c.restore();}
    const light=ease((time-30)/3);
    this.lantern(602,696,.27,light);
    if(light>0){this.rays(329,661,120,light*.6,time);c.save();c.globalAlpha=light*.7;this.shape("M311 718 L313 632 Q325 614 346 634 L355 720 Z","#e7b555");c.restore();}
    const p=clamp((time-16)/24), x=430+Math.sin(p*Math.PI*2)*205, y=460-Math.sin(p*Math.PI)*90;
    this.firefly(x,y,.7,time,true,Math.cos(time)*.1);
    if(time>21&&time<28)this.leaf(x+10,y-45,.55,-1.6+Math.sin(time*2)*.07);
    if(night>0){for(let i=0;i<26;i++){const x=90+(i*137)%780,y=110+(i*97)%260;this.star(x,y,2+i%3,night*(.55+.35*Math.sin(time+i)));}this.constellation(ease((time-36)/3),.7);}
  }
  private constellation(progress: number, alpha: number) {
    const c=this.ctx, points=[[367,283],[448,185],[524,282],[594,219],[634,299]];
    c.save();c.globalAlpha*=alpha;c.strokeStyle=C.gold;c.lineWidth=1.7;c.setLineDash([7,5]);
    for(let i=1;i<points.length;i++){const p=clamp(progress*4-(i-1));if(p){c.beginPath();c.moveTo(...points[i-1] as [number,number]);c.lineTo(mix(points[i-1][0],points[i][0],p),mix(points[i-1][1],points[i][1],p));c.stroke();}}
    c.setLineDash([]);points.forEach(([x,y],i)=>{if(progress*5>i)this.star(x,y,5);});c.restore();
  }
  private feedback(s: FilmSnapshot, time: number) {
    const c=this.ctx, paid=s.mode==="paid", signed=s.mode==="celebrate";
    this.lantern(478,754,1.2,paid||signed?1:.45);
    const e=s.effect;
    if(e?.kind==="item"){
      const p=clamp(e.age/1.6),x=mix(150,570,ease(p)),y=510-Math.sin(p*Math.PI)*100;
      this.firefly(x,y,1,time,true,-.12);this.leaf(x-32,y+29,.21,.8);
      for(let i=0;i<5;i++)this.star(x-35-i*17,y+10+Math.sin(i)*6,2.2,(1-p)*.6);
    }else this.firefly(645+Math.sin(time)*8,490+Math.sin(time*1.5)*9,.95,time,true,-.2);
    if(paid){this.rays(478,591,310,e?Math.sin(clamp(e.age/4)*Math.PI)*.8+.25:.25,time);if(e)for(let i=0;i<12;i++){const a=i/12*Math.PI*2,p=ease(e.age/2);this.star(478+Math.cos(a)*(150+p*160),570+Math.sin(a)*(140+p*95),5,(1-clamp((e.age-2)/2)));}}
    if(signed)this.constellation(e?ease(e.age/1.7):1,1);
    c.font="500 27px serif";c.textAlign="center";c.fillStyle=C.ink;
    if(paid)c.fillText("謝謝，帶著好心情出發。",480,849);
    if(signed)c.fillText("約定收好了，一起去山裡。",480,849);
  }
  draw(s: FilmSnapshot, width: number, height: number, clock: number) {
    const c=this.ctx; c.clearRect(0,0,width,height);c.fillStyle=C.paper;c.fillRect(0,0,width,height);
    const size=Math.min(width,height)*1.04, scale=size/960;
    c.save();c.translate((width-size)/2,(height-size)/2);c.scale(scale,scale);this.paper();
    const t=s.time;
    // 鏡頭的接點採圓形光暈擴張，從營燈內的光進到小營地。
    const campIn=ease((t-11)/3),campOut=ease((t-42)/4),campAmount=campIn*(1-campOut);
    c.save();c.globalAlpha=1-s.focus;this.macro(t<42?t:0);
    if(campAmount>0){c.save();c.beginPath();c.arc(500,535,780*campAmount,0,Math.PI*2);c.clip();this.paper();this.landscape(t);c.restore();}c.restore();c.restore();
    if(s.focus>.001){
      // 結帳只佔上方安全區；完成動畫可使用較大的上半部，正文和金額仍由原 UI 顯示。
      const compact=s.mode==="cart", region=compact?height*.20:height*.52;
      const w=Math.min(width*.94,region*1.52),k=w/960;
      c.save();c.globalAlpha=s.focus;c.translate((width-w)/2,(compact?height*.105:0)+(region-w*.68)/2-155*k);c.scale(k,k);this.feedback(s,s.reduced?0:clock);c.restore();
    }
  }
}
