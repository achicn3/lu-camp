// 客人點餐頁的純邏輯（不碰 DOM，好測）。

const TAIPEI_HOUR = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Taipei",
  hour: "numeric",
  hourCycle: "h23",
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
export function itemBadge(item: { remaining: number | null }): string | null {
  if (item.remaining === null) return null;
  return item.remaining === 0 ? "今日售完" : `剩 ${item.remaining} 份`;
}

const TABLE_PATH = /^\/t\/([A-Za-z0-9_-]{16,64})\/?$/;

export function tableCodeFromPath(pathname: string): string | null {
  return TABLE_PATH.exec(pathname)?.[1] ?? null;
}
