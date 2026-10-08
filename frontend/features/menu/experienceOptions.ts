// 手沖體驗卡的選項清單（後台列表與編輯頁共用）。
import type { components } from "@/lib/api-types";

type Write = components["schemas"]["MenuExperienceWriteRequest"];
export type Theme = NonNullable<Write["theme"]>;
export type Art = NonNullable<Write["art"]>;
export type Effect = NonNullable<Write["effect"]>;

export const THEMES: { value: Theme; label: string }[] = [
  { value: "peach", label: "蜜桃粉" },
  { value: "honey", label: "蜂蜜奶油" },
  { value: "citrus", label: "柑橘霧藍" },
  { value: "wine", label: "酒紅" },
  { value: "forest", label: "森林綠" },
  { value: "ink", label: "夜墨" },
];
export const ARTS: { value: Art; label: string }[] = [
  { value: "peach", label: "蜜桃與荔枝" },
  { value: "vanilla", label: "香草與蜂蜜" },
  { value: "citrus", label: "檸檬與佛手柑" },
  { value: "rum", label: "酒杯與櫻桃" },
  { value: "none", label: "不放插畫" },
];
export const EFFECTS: { value: Effect; label: string }[] = [
  { value: "random", label: "每次隨機" },
  { value: "soar", label: "升空翻轉" },
  { value: "truck", label: "咖啡車送卡" },
  { value: "smash", label: "砸地插卡" },
  { value: "seal", label: "金光開封" },
  { value: "shuffle", label: "洗牌抽出" },
  { value: "bloom", label: "豆子綻放" },
];
export const MAX_INCLUDES = 5;
/** 店主 2026-10-08 認可的展示稿內容，新卡先帶入、可改。 */
export const DEFAULT_INCLUDES = [
  { title: "咖啡豆", detail: "這支豆子現磨、單杯份量" },
  { title: "完整器材使用", detail: "手沖壺、濾杯、磨豆機、秤，店員在旁協助" },
  { title: "現場體驗", detail: "約 20 分鐘，自己沖出今天的那一杯" },
];

export function apiDetail(error: unknown, fallback: string): string {
  return error && typeof error === "object" && "detail" in error && typeof error.detail === "string"
    ? error.detail
    : fallback;
}
