// 菜單快照的形狀（店內 backend app/modules/onlineorder/snapshot.py 產生）。
export interface MenuOptionView {
  id: number;
  name: string;
  price_delta: number;
  available: boolean;
  remaining: number | null;
}

export interface OptionGroupView {
  id: number;
  name: string;
  min_select: number;
  max_select: number;
  options: MenuOptionView[];
}

export interface MenuItemView {
  id: number;
  name: string;
  description: string | null;
  category_id: number | null;
  unit_price: number;
  photo: string | null;
  available: boolean;
  remaining: number | null;
  option_groups: OptionGroupView[];
  presentation?: MenuPresentation;
  /** 人氣名次（docs/63 §7）：1＝人氣 No.1、2–3＝人氣推薦；沒上榜沒有這欄。 */
  popularity?: number;
}

export interface MenuPresentation {
  flavor_description: string | null;
  audience_description: string | null;
  is_recommended: boolean;
  is_new: boolean;
  limited_on: string | null;
  show_remaining: boolean;
  low_stock_threshold: number;
  hide_sold_out: boolean;
  /** 加購角色（docs/63 §6）；舊快照沒有這欄。 */
  role?: UpsellRole | null;
}

export type UpsellRole = "coffee" | "dessert" | "experience" | "bean" | "drip" | "other";
export const UPSELL_ROLES: readonly UpsellRole[] = ["coffee", "dessert", "experience", "bean", "drip", "other"];
export const BREW_THEMES = ["peach", "honey", "citrus", "wine", "forest", "ink"] as const;
export const BREW_ARTS = ["peach", "vanilla", "citrus", "rum", "none"] as const;
export const BREW_EFFECTS = ["random", "soar", "truck", "smash", "seal", "shuffle", "bloom"] as const;

/** 手沖體驗卡（docs/63 §4）：原品項＋預選選項的另一種呈現；價格照原品項算。 */
export interface MenuExperienceView {
  id: number;
  item_id: number;
  option_ids: number[];
  title: string;
  tag: string | null;
  origin: string | null;
  notes: string | null;
  description: string | null;
  includes: { title: string; detail: string | null }[];
  theme: (typeof BREW_THEMES)[number];
  art: (typeof BREW_ARTS)[number];
  effect: (typeof BREW_EFFECTS)[number];
}

export const RETAIL_ROLES = ["bean", "drip"] as const;

/** 帶回家零售商品（docs/63 §13）：價格是商品含稅售價、remaining 是店內現量（扣掉線上保留）。 */
export interface MenuRetailView {
  id: number;
  name: string;
  description: string | null;
  category: string | null;
  unit_price: number;
  photo: string | null;
  role: (typeof RETAIL_ROLES)[number] | null;
  available: boolean;
  remaining: number;
}

export interface MenuSnapshot {
  version: number;
  published_at: string;
  store_name: string;
  font: string | null;
  categories: { id: number; name: string }[];
  items: MenuItemView[];
  /** 舊快照沒有這欄（當成空的）。 */
  experiences?: MenuExperienceView[];
  /** 帶回家零售商品；舊快照沒有這欄（當成空的）。 */
  retail?: MenuRetailView[];
  /** 「不知道喝什麼」引導推薦；沒設定或沒有可推薦品項時沒有這欄。 */
  quiz?: MenuQuizView;
}

/** 答案勾的品項：菜單品項或手沖體驗卡（docs/63 §2 M2a）。 */
export interface QuizRef { kind: "item" | "experience"; id: number }
export interface MenuQuizView {
  questions: { prompt: string; options: { label: string; items: QuizRef[] }[] }[];
}

export interface TableView {
  label: string;
  service_mode: "DINE_IN" | "TAKEOUT";
}
