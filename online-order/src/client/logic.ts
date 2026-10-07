// 客人點餐頁的純邏輯（不碰 DOM，好測）。
import type { MenuItemView, MenuPresentation, MenuSnapshot } from "./types";

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
