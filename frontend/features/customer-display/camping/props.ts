// 營地道具（精緻版）：木棧台、營桌、手沖壺、濾杯與分享壺、咖啡豆袋、營燈、露營椅、營火堆。
// 每樣都是：底色（帶紙紋顏料深淺）＋手繪明暗＋材質筆觸＋局部細節；金屬、木頭、布各用不同筆觸。
import { stone } from "./nature";
import { INK, INK_SOFT, type Rng, fillPath, g, h, hatchArea, inkPath, shadow, shape } from "./svg";

function f(v: number): string {
  return (Math.round(v * 10) / 10).toString();
}

/** 木紋：沿著木板方向的細波浪線，偶爾一個節疤。 */
export function woodGrain(r: Rng, x0: number, x1: number, y0: number, y1: number, lines: number): string {
  let d = "";
  for (let i = 0; i < lines; i += 1) {
    const y = y0 + ((i + 0.5) / lines) * (y1 - y0) + (r() - 0.5) * 2;
    const xa = x0 + r() * (x1 - x0) * 0.3;
    const xb = xa + (x1 - x0) * (0.3 + r() * 0.5);
    const amp = (y1 - y0) * 0.12;
    d += `M${f(xa)} ${f(y)} C${f(xa + (xb - xa) * 0.3)} ${f(y - amp)} ${f(xa + (xb - xa) * 0.6)} ${f(y + amp)} ${f(Math.min(xb, x1))} ${f(y)} `;
  }
  let out = h("path", { d, stroke: "#7a5634", "stroke-width": 0.9, fill: "none", opacity: 0.6, "stroke-linecap": "round" });
  if (r() > 0.5) {
    const kx = x0 + (x1 - x0) * (0.2 + r() * 0.6);
    const ky = (y0 + y1) / 2;
    out += h("ellipse", { cx: kx, cy: ky, rx: 5, ry: Math.max(1.5, (y1 - y0) * 0.2), fill: "#8d6440", stroke: "#6b4a2e", "stroke-width": 0.8, opacity: 0.8 });
  }
  return out;
}

/** 木棧台（透視梯形，一片片木板深淺不同、有木紋、釘子、前緣厚度與底下的影子）。 */
export function deck(r: Rng): string {
  const rows = 7;
  let planks = "";
  const tones = ["#c99a63", "#c3925b", "#cea06a", "#bf8f58", "#d0a46f"];
  for (let i = 0; i < rows; i += 1) {
    const t0 = i / rows;
    const t1 = (i + 1) / rows;
    const ya = 1110 + 90 * t0;
    const yb = 1110 + 90 * t1;
    const la = 470 - 50 * t0;
    const lb = 470 - 50 * t1;
    const ra = 950 + 40 * t0;
    const rb = 950 + 40 * t1;
    const d = `M${f(la)} ${f(ya)} L${f(ra)} ${f(ya)} L${f(rb)} ${f(yb)} L${f(lb)} ${f(yb)} Z`;
    planks += fillPath(d, tones[i % tones.length] ?? "#c99a63");
    planks += woodGrain(r, la + 20, ra - 20, ya + 2, yb - 2, 2);
    // 木板接縫（每排錯開）
    const seam = la + (ra - la) * (0.3 + (i % 3) * 0.2);
    planks += h("path", { d: `M${f(seam)} ${f(ya + 1)} L${f(seam - 3)} ${f(yb - 1)}`, stroke: "#6b4a2e", "stroke-width": 1, opacity: 0.8 });
    for (const x of [la + 14, seam - 8, seam + 6, ra - 14]) {
      planks += h("circle", { cx: x, cy: (ya + yb) / 2 - 1, r: 1.3, fill: "#4a3527" });
    }
    planks += h("path", { d: `M${f(la)} ${f(yb)} L${f(rb)} ${f(yb)}`, stroke: "#6b4a2e", "stroke-width": 1.2, opacity: 0.9 });
    // 小刮痕
    if (r() > 0.4) {
      const sx = la + 60 + r() * (ra - la - 140);
      planks += h("path", { d: `M${f(sx)} ${f(ya + 5)} l${f(10 + r() * 14)} ${f(1 + r() * 2)} M${f(sx + 4)} ${f(ya + 8)} l${f(6 + r() * 8)} 1`, stroke: "#e2c08f", "stroke-width": 0.8, opacity: 0.8, fill: "none" });
    }
  }
  let ends = "";
  for (let x = 432; x < 990; x += 44) ends += `M${x} 1203 L${x} 1220 `;
  return (
    h("path", { d: "M412 1226 L1000 1226 L996 1236 L416 1236 Z", fill: INK, opacity: 0.18, filter: "url(#cs-pencil)" }) +
    planks +
    fillPath("M420 1200 L990 1200 L990 1222 L420 1222 Z", "#9c6f41") +
    hatchArea("M420 1200 L990 1200 L990 1222 L420 1222 Z", "cs-hatch-fine", 0.8) +
    h("path", { d: ends, stroke: "#5c3f27", "stroke-width": 1, opacity: 0.7, fill: "none" }) +
    inkPath("M470 1110 L950 1110 L990 1200 L420 1200 Z", 2.6) +
    inkPath("M420 1200 L420 1222 L990 1222 L990 1200", 2.2)
  );
}

/** 營桌：三片木板桌面、桌板厚度、X 型金屬腳（管子有亮面、接點螺絲）。 */
export function campTable(r: Rng): string {
  let top = fillPath("M660 1010 L940 1010 L934 1030 L666 1030 Z", "#bd8450");
  top += fillPath("M662 1004 L938 1004 L940 1012 L660 1012 Z", "#d9a66f");
  for (const x of [753, 846]) top += h("path", { d: `M${x} 1004 L${x} 1012`, stroke: "#7a5634", "stroke-width": 1 });
  top += woodGrain(r, 670, 930, 1014, 1028, 2);
  const tube = (d: string) =>
    h("path", { d, stroke: "#6f7478", "stroke-width": 5, "stroke-linecap": "round", fill: "none" }) +
    h("path", { d, stroke: "#b8bcbf", "stroke-width": 1.4, "stroke-linecap": "round", fill: "none", transform: "translate(-1 -1)" }) +
    inkPath(d, 1.2);
  return (
    shadow(800, 1146, 150, 9, 0.18) +
    h("path", { d: "M660 1147 l24 0 M916 1147 l24 0", stroke: INK, "stroke-width": 3, opacity: 0.32, "stroke-linecap": "round" }) +
    tube("M690 1030 L672 1140") +
    tube("M910 1030 L928 1140") +
    tube("M684 1034 L922 1138") +
    tube("M916 1034 L678 1138") +
    h("circle", { cx: 800, cy: 1086, r: 4, fill: "#9aa0a4", stroke: INK, "stroke-width": 1.2 }) +
    shape("M668 1140 L680 1140 L680 1146 L664 1146 Z M920 1140 L932 1140 L936 1146 L920 1146 Z", "#4d4f52", 1.2) +
    top +
    hatchArea("M660 1012 L940 1012 L934 1030 L666 1030 Z", "cs-hatch-fine", 0.7) +
    inkPath("M660 1004 L940 1004 L940 1012 L934 1030 L666 1030 L660 1012 Z", 2.6) +
    inkPath("M660 1012 L940 1012", 1.2)
  );
}

/** 細嘴手沖壺：壺身、壺蓋（木頭蓋鈕）、鵝頸壺嘴（接壺身處有一圈頸圈）、纏繩壺把（上下兩個固定座）、淡淡的金屬反光。 */
export function kettle(): string {
  const body = "M742 1004 C738 990 740 966 750 952 L790 952 C800 966 802 990 798 1004 Z";
  return (
    h("ellipse", { cx: 770, cy: 1005, rx: 32, ry: 3.5, fill: INK, opacity: 0.28 }) +
    fillPath(body, "#cfd5d8") +
    fillPath("M776 952 L790 952 C800 966 802 990 798 1004 L780 1004 C786 986 784 966 776 952 Z", "#a3abb0") +
    hatchArea("M782 956 L790 952 C800 966 802 990 798 1004 L786 1004 C790 986 790 968 782 956 Z", "cs-hatch-fine") +
    h("path", { d: "M752 960 C749 974 749 988 751 998", stroke: "#eef4f8", "stroke-width": 2.6, "stroke-linecap": "round", fill: "none", opacity: 0.55 }) +
    h("path", { d: "M760 958 L759 1000 M771 956 L771 1002", stroke: "#eef0ec", "stroke-width": 1, fill: "none", opacity: 0.5 }) +
    inkPath("M741 996 C760 999 782 999 799 996", 0.9) +
    // 壺蓋：金屬蓋＋木頭蓋鈕
    shape("M750 952 C752 942 788 942 790 952 Z", "#bcc0bb", 1.9) +
    inkPath("M754 948 C762 945 778 945 786 948", 0.8) +
    shape("M765 943 C764 936 776 936 775 943 Z", "#8a5a34", 1.3) +
    // 鵝頸壺嘴＋頸圈
    h("path", { d: "M796 990 C808 987 809 975 815 966 C820 959 828 955 834 954", stroke: "#c4c8c3", "stroke-width": 5, fill: "none", "stroke-linecap": "round" }) +
    h("path", { d: "M800 986 C808 983 809 975 814 968", stroke: "#eef0ec", "stroke-width": 1, fill: "none", opacity: 0.7 }) +
    inkPath("M796 986 C806 984 807 973 813 964 C819 957 827 953 834 952 M798 994 C812 991 813 977 817 968 C822 961 830 957 834 957 M834 952 L835 957", 1.5) +
    shape("M793 984 L801 983 L802 996 L794 997 Z", "#9ea39e", 1.1) +
    // 壺把：纏繩＋兩個固定座
    h("path", { d: "M744 962 C722 962 718 996 742 998", stroke: "#6b4a33", "stroke-width": 6, fill: "none", "stroke-linecap": "round" }) +
    h("path", { d: "M728 968 l4 3 M725 976 l5 2 M725 984 l5 1 M728 992 l4 -1", stroke: "#3f2c20", "stroke-width": 1, fill: "none" }) +
    inkPath("M744 958 C716 958 712 1000 742 1002", 1.5) +
    shape("M740 958 L747 958 L747 966 L740 966 Z M739 994 L746 994 L746 1002 L739 1002 Z", "#9ea39e", 1) +
    inkPath(body, 2.3)
  );
}

/** 濾杯＋分享壺：陶瓷濾杯（肋條、底座、露出的濾紙摺邊、正在滴的咖啡）、玻璃分享壺（壺口、把手、深淺兩層咖啡、反光）。 */
export function dripperSet(): string {
  const glass = "M846 1004 C842 990 844 978 850 972 L880 972 C886 978 888 990 884 1004 Z";
  return (
    h("ellipse", { cx: 865, cy: 1005, rx: 24, ry: 3, fill: INK, opacity: 0.28 }) +
    fillPath(glass, "#e6f2f4", { opacity: 0.4 }) +
    fillPath("M845 1004 C843 996 844 990 846 986 L884 986 C886 990 887 996 885 1004 Z", "#6a4126") +
    fillPath("M845 1004 C844 999 844 996 845 994 L885 994 C886 996 886 999 885 1004 Z", "#472a17") +
    h("ellipse", { cx: 865, cy: 986, rx: 19, ry: 2.4, fill: "#8a5a36" }) +
    h("path", { d: "M852 976 L851 1000 M856 976 L855 984", stroke: "#fff", "stroke-width": 1.8, "stroke-linecap": "round", opacity: 0.8 }) +
    h("path", { d: glass, fill: "none", stroke: "#f7fbfc", "stroke-width": 2.2, opacity: 0.8, transform: "translate(1 0)" }) +
    h("path", { d: glass, fill: "none", stroke: INK_SOFT, "stroke-width": 1.3, filter: "url(#cs-rough)" }) +
    inkPath("M848 972 L882 972", 1.1) +
    shape("M884 978 c10 0 11 16 0 18 l0 -4 c6 -1 5 -10 0 -10 Z", "#e3f0f1", 1.2) +
    // 正在滴的咖啡
    h("path", { d: "M865 972 L865 980", stroke: "#5a3620", "stroke-width": 1.4, "stroke-linecap": "round" }) +
    h("circle", { cx: 865, cy: 983, r: 1.3, fill: "#5a3620" }) +
    // 濾杯（陶瓷）＋底座
    shape("M850 966 L880 966 L882 972 L848 972 Z", "#e7e2d8", 1.4) +
    shape("M838 950 L892 950 L880 966 L850 966 Z", "#efe7da", 1.9) +
    hatchArea("M872 950 L892 950 L880 966 L868 966 Z", "cs-hatch-fine", 0.8) +
    h("path", { d: "M841 950 l4 -6 l4 6 l4 -6 l4 6 l4 -6 l4 6 l4 -6 l4 6 l4 -6 l4 6 l4 -6 l4 6", stroke: "#b9b2a4", "stroke-width": 0.9, fill: "#fbf9f3" }) +
    inkPath("M852 955 L856 964 M865 955 L865 964 M878 955 L874 964", 0.8)
  );
}

/** 咖啡豆袋：牛皮紙、上緣捲起、小標籤。 */
export function beanBag(): string {
  const bag = "M900 1004 L902 966 L930 966 L932 1004 Z";
  return (
    h("ellipse", { cx: 916, cy: 1005, rx: 18, ry: 2.5, fill: INK, opacity: 0.25 }) +
    fillPath(bag, "#c9a77a") +
    hatchArea("M918 966 L930 966 L932 1004 L920 1004 Z", "cs-hatch-fine") +
    inkPath("M905 972 L906 1000 M912 990 l4 6", 0.7, { opacity: 0.6 }) +
    shape("M900 966 L932 966 L931 958 L901 958 Z", "#b89366", 1.3) +
    shape("M906 978 L926 978 L926 992 L906 992 Z", "#f4efe4", 1) +
    h("path", { d: "M910 983 L922 983 M910 987 L919 987", stroke: INK_SOFT, "stroke-width": 0.9 }) +
    inkPath(bag, 1.9)
  );
}

/** 營燈：金屬底座與頂蓋、玻璃罩（反光）、燈芯與小火、柔和的暖色光暈。 */
export function lantern(x: number, y: number): string {
  return g(
    { transform: `translate(${x} ${y})` },
    h("circle", { cx: 0, cy: -24, r: 30, fill: "url(#cs-glow)", opacity: 0.35 }),
    h("ellipse", { cx: 0, cy: 1, rx: 16, ry: 2.5, fill: INK, opacity: 0.28 }),
    fillPath("M-10 -38 C-13 -30 -13 -16 -10 -8 L10 -8 C13 -16 13 -30 10 -38 Z", "#f8e3a4"),
    h("path", { d: "M-6 -34 C-8 -26 -8 -18 -6 -12", stroke: "#fff", "stroke-width": 1.8, fill: "none", opacity: 0.85 }),
    h("path", { d: "M0 -14 L0 -20", stroke: "#5a4638", "stroke-width": 1.2 }),
    fillPath("M0 -21 C-3 -24 -2 -29 0 -32 C2 -29 3 -24 0 -21 Z", "#f0a23c"),
    inkPath("M-10 -38 C-13 -30 -13 -16 -10 -8 M10 -38 C13 -30 13 -16 10 -8", 1.4),
    inkPath("M-4 -38 L-4 -8 M4 -38 L4 -8", 0.8, { opacity: 0.7 }),
    shape("M-14 -8 L14 -8 L13 0 L-13 0 Z", "#3f6b52", 1.7),
    shape("M-10 -46 L10 -46 L12 -38 L-12 -38 Z", "#3f6b52", 1.7),
    inkPath("M-8 -46 C-8 -60 8 -60 8 -46", 1.9),
  );
}

/** 露營椅：布面、車縫線、扶手帶、鋁管框架與接頭。 */
export function campChair(): string {
  const back = "M492 1064 L604 1064 L618 944 L484 946 Z";
  const seat = "M488 1064 L608 1064 L598 1090 L500 1090 Z";
  const tube = (d: string) => h("path", { d, stroke: "#55595c", "stroke-width": 4, "stroke-linecap": "round", fill: "none" }) + inkPath(d, 1.1);
  return (
    shadow(560, 1150, 96, 9, 0.18) +
    h("path", { d: "M466 1146 l16 0 M616 1146 l16 0", stroke: INK, "stroke-width": 3, opacity: 0.32, "stroke-linecap": "round" }) +
    tube("M500 1090 L476 1146 M598 1090 L622 1146 M496 1090 L626 1146 M602 1090 L472 1146") +
    fillPath(back, "#4f7a5f") +
    hatchArea("M560 946 L618 944 L604 1064 L572 1064 Z", "cs-hatch") +
    fillPath(seat, "#3e644c") +
    hatchArea(seat, "cs-hatch-fine", 0.8) +
    inkPath("M497 954 L500 1056 M605 954 L597 1056 M488 1000 L612 998", 1.1, { "stroke-dasharray": "4 4" }) +
    inkPath(back, 2.6) +
    inkPath(seat, 2.4) +
    shape("M478 1040 L492 1040 L492 1050 L478 1050 Z M606 1040 L622 1040 L622 1050 L606 1050 Z", "#2f4d3a", 1.4)
  );
}

/** 營火堆：石頭圈、灰燼與炭火、交叉的柴（樹皮紋、切面年輪）、地上被火烤暖的一圈。火焰是角色。 */
export function firePit(r: Rng): string {
  let stones = "";
  for (let i = 0; i < 9; i += 1) {
    const a = Math.PI * (0.05 + (i / 8) * 0.9);
    stones += stone(r, Math.cos(a) * -78, Math.sin(a) * 18 + 8, 14 + r() * 7);
  }
  const log = (d: string, end: [number, number]) =>
    fillPath(d, "#7a4d29") +
    hatchArea(d, "cs-hatch-fine", 0.8) +
    h("path", { d, fill: "none", stroke: "#4a2f1b", "stroke-width": 0.9, "stroke-dasharray": "10 6", opacity: 0.6, transform: "translate(0 -3)" }) +
    inkPath(d, 2) +
    h("ellipse", { cx: end[0], cy: end[1], rx: 5, ry: 7, fill: "#d9b287", stroke: INK, "stroke-width": 1.4 }) +
    h("ellipse", { cx: end[0], cy: end[1], rx: 2.4, ry: 3.4, fill: "none", stroke: "#8d6440", "stroke-width": 0.8 });
  let embers = "";
  for (let i = 0; i < 10; i += 1) embers += h("circle", { cx: -36 + r() * 72, cy: -2 + r() * 12, r: 1.2 + r() * 1.6, fill: r() > 0.5 ? "#f08a2c" : "#c9553a" });
  return (
    h("ellipse", { cx: 0, cy: 4, rx: 130, ry: 30, fill: "#e0a060", opacity: 0.14 }) +
    h("ellipse", { cx: 0, cy: 6, rx: 70, ry: 16, fill: "#5a5550", filter: "url(#cs-pencil)" }) +
    embers +
    stones +
    log("M-58 2 L50 -20 L56 -8 L-52 14 Z", [53, -14]) +
    log("M-56 -20 L52 2 L46 14 L-60 -8 Z", [-58, -14]) +
    log("M-8 -8 L30 -44 L38 -38 L0 -2 Z", [34, -42]) +
    inkPath("M-40 12 L-18 -6 M-44 10 l-6 -4", 1.4, { stroke: "#5a3d28" })
  );
}

/**
 * 結帳時「坐在桌前」的第一人稱桌面（畫面座標，1000×1400）：稍微俯視的木頭桌面由下往上滑進來，
 * 手帳夾板放在上面；右上角一杯冒著蒸氣的咖啡。
 */
export function povDesk(r: Rng): string {
  const top = 520;
  const rows = 7;
  let planks = "";
  const tones = ["#c99a63", "#c3925b", "#cea06a", "#bf8f58"];
  let y = top;
  for (let i = 0; i < rows; i += 1) {
    const hgt = 70 + i * 22;
    const d = `M-200 ${y} L1200 ${y} L1200 ${y + hgt} L-200 ${y + hgt} Z`;
    planks += fillPath(d, tones[i % tones.length] ?? "#c99a63");
    planks += woodGrain(r, -40, 1040, y + 6, y + hgt - 6, 3);
    planks += h("path", { d: `M-200 ${y + hgt} L1200 ${y + hgt}`, stroke: "#6b4a2e", "stroke-width": 1.6, opacity: 0.8 });
    y += hgt;
  }
  return (
    planks +
    h("rect", { x: -200, y: top, width: 1400, height: 60, fill: INK, opacity: 0.12 }) +
    inkPath(`M-200 ${top} L1200 ${top}`, 3) +
    fillPath(`M-200 ${top - 18} L1200 ${top - 18} L1200 ${top} L-200 ${top} Z`, "#a8743f") +
    inkPath(`M-200 ${top - 18} L1200 ${top - 18}`, 2) +
    // 桌角一杯咖啡（蒸氣會慢慢飄）
    g(
      { class: "cs-pov-mug", transform: "translate(905 560)" },
      h("ellipse", { cx: 0, cy: 40, rx: 46, ry: 9, fill: INK, opacity: 0.28 }),
      shape("M-36 -30 L36 -30 L32 38 L-32 38 Z", "#f4efe4", 2.4),
      h("ellipse", { cx: 0, cy: -30, rx: 36, ry: 8, fill: "#5a3620", stroke: INK, "stroke-width": 1.6 }),
      inkPath("M36 -18 c22 0 22 34 -2 34", 2.2),
      h("path", { d: "M-24 -20 L-22 30", stroke: "#fff", "stroke-width": 4, opacity: 0.7, "stroke-linecap": "round" }),
      g({ class: "cs-pov-steam", fill: "none", stroke: INK_SOFT, "stroke-width": 1.8, "stroke-linecap": "round", opacity: 0.55, filter: "url(#cs-rough)" }, h("path", { d: "M-8 -44 c-12 -18 12 -26 0 -46 c-12 -18 12 -26 0 -44" }), h("path", { d: "M10 -46 c-10 -16 10 -24 0 -40" })),
    )
  );
}

/** 硬殼保冷箱：厚蓋、扣具與防撞角；原點在底部中央。 */
export function hardCooler(): string {
  return g({ class: "cs-hard-cooler" },
    shape("M-50 -64 L50 -64 L47 0 L-47 0 Z", "#718779", 2.2),
    shape("M-54 -76 L54 -76 L54 -62 L-54 -62 Z", "#e6dfcc", 2.2),
    inkPath("M-44 -55 L-42 -8 M44 -55 L42 -8 M-30 -48 L30 -48", 1.2),
    shape("M-34 -68 h10 v22 h-10 Z M24 -68 h10 v22 h-10 Z", "#34433f", 1.3),
    inkPath("M-16 -77 v-9 h32 v9", 3),
  );
}

/** 折疊推車，握把在右上方，載著同一只保冷箱。 */
export function campingWagon(): string {
  return g({ class: "cs-wagon" },
    shadow(0, 0, 85, 7),
    g({ transform: "translate(-6 -34) scale(0.85)" }, hardCooler()),
    shape("M-78 -68 L76 -68 L64 -18 L-66 -18 Z", "#687666", 2.4, "cs-hatch-fine"),
    inkPath("M-66 -65 L62 -21 M64 -65 L-62 -21 M-78 -70 L78 -70 M70 -38 L113 -103 L131 -103", 4, { stroke: "#394443" }),
    h("circle", { cx: -52, cy: -7, r: 13, fill: "#34403e", stroke: INK, "stroke-width": 2 }),
    h("circle", { cx: 52, cy: -7, r: 13, fill: "#34403e", stroke: INK, "stroke-width": 2 }),
    h("circle", { cx: -52, cy: -7, r: 5, fill: "#b3b7ae" }),
    h("circle", { cx: 52, cy: -7, r: 5, fill: "#b3b7ae" }),
  );
}

/** 可攜式電源，夜間供應充電營燈。 */
export function powerStation(): string {
  return g({ class: "cs-power-station" },
    shadow(0, 2, 45, 6),
    shape("M-42 -56 Q-42 -64 -34 -64 H34 Q42 -64 42 -56 V-4 Q42 2 34 2 H-34 Q-42 2 -42 -4 Z", "#414e50", 2.2),
    inkPath("M-24 -64 V-78 H24 V-64", 6, { stroke: "#b5bbae" }),
    h("rect", { x: -26, y: -48, width: 31, height: 18, rx: 3, fill: "#a8c5b1", stroke: INK, "stroke-width": 1.2 }),
    inkPath("M-20 -42 h4 v7 h-4 Z M-12 -42 h4 v7 h-4 Z", 1.3),
    h("circle", { cx: 22, cy: -37, r: 8, fill: "#253538", stroke: "#aab7b2", "stroke-width": 1.5 }),
    inkPath("M-28 -17 h13 M-28 -10 h13 M0 -16 h6 M0 -10 h6 M18 -16 h12", 2),
    h("circle", { class: "cs-power-led", cx: 32, cy: -12, r: 2.5, fill: "#b8da87", opacity: 0 }),
  );
}

/** 小型電動打氣機與接到帳篷氣嘴的軟管。 */
export function electricPump(): string {
  return g({ class: "cs-electric-pump" },
    inkPath("M18 -10 C62 -8 74 24 120 8", 5, { stroke: "#485454" }),
    shape("M-18 -26 H18 V0 H-18 Z", "#455558", 2),
    inkPath("M-10 -27 v-9 h20 v9 M-12 -17 h16 M-12 -10 h16", 2),
    h("circle", { class: "cs-pump-led", cx: 10, cy: -20, r: 3, fill: "#c6dd91", opacity: 0 }),
  );
}

/** 充電營燈：霧面燈罩、金屬頂蓋、折疊提環。 */
export function ledLantern(): string {
  return g({ class: "cs-led-lantern" },
    h("ellipse", { class: "cs-led-glow", cx: 0, cy: -24, rx: 90, ry: 74, fill: "url(#cs-ambient)", opacity: 0 }),
    shape("M-13 -40 H13 L16 -4 H-16 Z", "#dfdfc8", 1.6),
    h("path", { class: "cs-led-light", d: "M-11 -36 H11 L13 -7 H-13 Z", fill: "#ffe7a0", opacity: 0 }),
    shape("M-18 -44 H18 V-37 H-18 Z M-18 -6 H18 V0 H-18 Z", "#455558", 1.6),
    inkPath("M-10 -44 V-55 Q0 -67 10 -55 V-44", 2),
  );
}
