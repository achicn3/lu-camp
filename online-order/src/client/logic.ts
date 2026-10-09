// 客人點餐頁的純邏輯（不碰 DOM，好測）。
import type {
  MenuExperienceView, MenuItemView, MenuPresentation, MenuRetailView, MenuSnapshot, OptionGroupView, QuizRef, UpsellRole,
} from "./types";
import { isRetailLine } from "../pricing";
import type { CartLine } from "./cart";

const TAIPEI_HOUR = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Taipei",
  hour: "numeric",
  hourCycle: "h23",
});
const TAIPEI_DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
});

/** 依台北時間換問候語（店主 2026-10-02 定案）。晚上不只問喝的，甜點輕食也照顧到。 */
export function greeting(now: Date): string {
  const hour = Number(TAIPEI_HOUR.format(now));
  if (hour >= 5 && hour < 11) return "早安，今天想喝點什麼？";
  if (hour >= 11 && hour < 17) return "午安，下午想來點什麼？";
  return "晚安，今晚想來點什麼？";
}

export function money(amount: number): string {
  return `$${amount.toLocaleString("en-US")}`;
}

/** 有選項的品項價格會變（加購），所以標「起」。 */
export function priceText(item: { unit_price: number; option_groups: unknown[] }): string {
  return item.option_groups.length > 0 ? `${money(item.unit_price)} 起` : money(item.unit_price);
}

/** 每日限量的小標：售完／剩幾份；不限量不顯示。 */
export function itemBadge(item: { remaining: number | null; presentation?: MenuPresentation }): string | null {
  if (item.remaining === 0) return "今日售完";
  if (item.remaining === null || item.presentation?.show_remaining === false ||
    item.remaining > (item.presentation?.low_stock_threshold ?? 5)) return null;
  return item.remaining === 1 ? "最後 1 份" : `今天剩 ${item.remaining} 份`;
}

/** 今日限定只看台北日期，不必為了跨日重發快照。 */
export function presentationBadges(item: { presentation?: MenuPresentation }, now: Date): string[] {
  const settings = item.presentation;
  if (!settings) return [];
  const badges: string[] = [];
  if (settings.is_recommended) badges.push("露坑推薦");
  if (settings.is_new) badges.push("新品");
  if (settings.limited_on === TAIPEI_DAY.format(now)) badges.push("今日限定");
  return badges;
}

/** 必選群組不足以組成合法選擇時，品項也不能加入。只讀原始可售狀態。 */
export function itemSoldOut(item: MenuItemView): boolean {
  return !item.available || item.remaining === 0 || item.option_groups.some((group) =>
    group.max_select < group.min_select ||
    group.options.filter((option) => option.available && option.remaining !== 0).length < group.min_select,
  );
}

/** 按原分類位置穩定後置售完項目，保留來源順序與庫存。 */
export function visibleItems(items: MenuItemView[]): MenuItemView[] {
  const visible = items.filter((item) => !(item.presentation?.hide_sold_out && itemSoldOut(item)));
  const categories = new Map<number | null, MenuItemView[]>();
  for (const item of visible) {
    const group = categories.get(item.category_id) ?? [];
    group.push(item); categories.set(item.category_id, group);
  }
  for (const group of categories.values()) group.sort((a, b) => Number(itemSoldOut(a)) - Number(itemSoldOut(b)));
  const positions = new Map<number | null, number>();
  return visible.map((item) => {
    const index = positions.get(item.category_id) ?? 0;
    positions.set(item.category_id, index + 1);
    return categories.get(item.category_id)![index]!;
  });
}

const TABLE_PATH = /^\/t\/([A-Za-z0-9_-]{16,64})\/?$/;

export function tableCodeFromPath(pathname: string): string | null {
  return TABLE_PATH.exec(pathname)?.[1] ?? null;
}

/** 首頁只使用已發布的人工推薦；保留後台順序，不推測人氣或品項角色。 */
export function homeSelection(menu: Pick<MenuSnapshot, "categories" | "items">) {
  const items = visibleItems(menu.items);
  return {
    recommended: menu.items.filter((item) => item.presentation?.is_recommended && !itemSoldOut(item)).slice(0, 3),
    categories: menu.categories.filter((category) => items.some((item) => item.category_id === category.id)),
  };
}

export interface ExperienceView {
  experience: MenuExperienceView;
  item: MenuItemView;
  /** 原品項＋預選選項的價格（還沒補選的必選項另計）。 */
  price: number;
  /** 還有必選項要客人補選、而且其中有加價的：價格標「起」。 */
  priceFrom: boolean;
  /** 預選沒涵蓋的必選群組：客人要自己選。 */
  pending: OptionGroupView[];
  soldOut: boolean;
}

/** 手沖體驗卡要怎麼呈現；原品項不在菜單上就不顯示（回 null）。 */
export function experienceView(menu: MenuSnapshot, experience: MenuExperienceView): ExperienceView | null {
  const item = menu.items.find((entry) => entry.id === experience.item_id);
  if (item === undefined) return null;
  const preset = new Set(experience.option_ids);
  let price = item.unit_price;
  let presetSoldOut = false;
  const pending: OptionGroupView[] = [];
  for (const group of item.option_groups) {
    const picked = group.options.filter((option) => preset.has(option.id));
    for (const option of picked) {
      price += option.price_delta;
      if (!option.available || option.remaining === 0) presetSoldOut = true;
    }
    if (picked.length < group.min_select) pending.push(group);
  }
  return {
    experience, item, price, pending,
    priceFrom: pending.some((group) => group.options.some((option) => option.price_delta > 0)),
    soldOut: presetSoldOut || itemSoldOut(item),
  };
}

/** 加購方向（docs/63 §6）：咖啡→甜點、甜點→咖啡、體驗→豆／濾掛、豆→濾掛／其他豆款。 */
const UPSELL_TARGETS: Partial<Record<UpsellRole, UpsellRole[]>> = {
  coffee: ["dessert"],
  dessert: ["coffee"],
  experience: ["bean", "drip"],
  bean: ["drip", "bean"],
};
const UPSELL_LIMIT = 2;

/** 加購推薦的一項：餐飲品項或帶回家商品（兩種 id 各自編號）。 */
export type UpsellPick =
  | ({ kind: "menu"; role: UpsellRole } & MenuItemView)
  | ({ kind: "retail"; role: UpsellRole } & MenuRetailView);

/** 購物車下方的「配個…？」：依角色挑可售、沒在車裡的，最多 2 項；略過過的方向不再推。
 *  餐飲照菜單順序在前，帶回家商品（咖啡豆／濾掛）接在後面。 */
export function upsellSuggestions(
  menu: MenuSnapshot,
  cart: readonly CartLine[],
  dismissed: ReadonlySet<UpsellRole>,
): UpsellPick[] {
  const byId = new Map(menu.items.map((entry) => [entry.id, entry]));
  const retail = menu.retail ?? [];
  const retailById = new Map(retail.map((entry) => [entry.id, entry]));
  const inCart = new Set<number>();
  const retailInCart = new Set<number>();
  const wanted: UpsellRole[] = [];
  for (const line of cart) {
    let role: UpsellRole | null | undefined;
    if (isRetailLine(line)) {
      retailInCart.add(line.catalog_product_id);
      role = retailById.get(line.catalog_product_id)?.role;
    } else {
      inCart.add(line.item_id);
      role = line.experience_id !== undefined ? "experience" : byId.get(line.item_id)?.presentation?.role;
    }
    for (const target of (role ? UPSELL_TARGETS[role] : undefined) ?? []) {
      if (!dismissed.has(target) && !wanted.includes(target)) wanted.push(target);
    }
  }
  if (wanted.length === 0) return [];
  const picks: UpsellPick[] = [];
  for (const entry of visibleItems(menu.items)) {
    const role = entry.presentation?.role;
    if (role != null && wanted.includes(role) && !inCart.has(entry.id) && !itemSoldOut(entry)) {
      picks.push({ kind: "menu", role, ...entry });
    }
  }
  for (const entry of retail) {
    if (entry.role !== null && wanted.includes(entry.role) && !retailInCart.has(entry.id) && !retailSoldOut(entry)) {
      picks.push({ kind: "retail", ...entry, role: entry.role });
    }
  }
  return picks.slice(0, UPSELL_LIMIT);
}

/** 帶回家商品不能賣：停售或沒貨。 */
export function retailSoldOut(product: MenuRetailView): boolean {
  return !product.available || product.remaining <= 0;
}

const RETAIL_OTHER = "其他";

/** 「帶回家」區依商品分類分組（照快照順序；沒分類的放「其他」最後）。 */
export function retailGroups(menu: MenuSnapshot): { category: string; products: MenuRetailView[] }[] {
  const groups = new Map<string, MenuRetailView[]>();
  for (const product of menu.retail ?? []) {
    const key = product.category ?? RETAIL_OTHER;
    groups.set(key, [...(groups.get(key) ?? []), product]);
  }
  const ordered = [...groups.entries()].filter(([key]) => key !== RETAIL_OTHER);
  const other = groups.get(RETAIL_OTHER);
  if (other) ordered.push([RETAIL_OTHER, other]);
  return ordered.map(([category, products]) => ({ category, products }));
}

const QUIZ_PICKS = 3;

/**
 * 「不知道喝什麼」的推薦（docs/63 §2 M2a）：客人每題選一個答案（answers[題] = 答案序號），
 * 每個答案勾的品項各加 1 分；分數高的排前面、同分照勾的先後。售完、看不到、已不在菜單上的不推。
 * 回 1 主推＋最多 2 備選；一個都對不上回空陣列（客人頁改給完整菜單，不捏造推薦）。
 */
export function quizResult(menu: MenuSnapshot, answers: number[]): QuizRef[] {
  const shown = new Set(visibleItems(menu.items).filter((item) => !itemSoldOut(item)).map((item) => item.id));
  const experiences = new Set((menu.experiences ?? [])
    .filter((experience) => {
      const view = experienceView(menu, experience);
      return view !== null && !view.soldOut && shown.has(view.item.id);
    })
    .map((experience) => experience.id));
  const scores = new Map<string, { ref: QuizRef; score: number; order: number }>();
  (menu.quiz?.questions ?? []).forEach((question, index) => {
    const option = question.options[answers[index] ?? -1];
    for (const ref of option?.items ?? []) {
      const sellable = ref.kind === "item" ? shown.has(ref.id) : experiences.has(ref.id);
      if (!sellable) continue;
      const key = `${ref.kind}:${ref.id}`;
      const entry = scores.get(key) ?? { ref, score: 0, order: scores.size };
      entry.score += 1;
      scores.set(key, entry);
    }
  });
  return [...scores.values()]
    .sort((a, b) => b.score - a.score || a.order - b.order)
    .slice(0, QUIZ_PICKS)
    .map((entry) => entry.ref);
}
