// 客人點餐頁的純邏輯（不碰 DOM，好測）。
import type {
  MenuExperienceView, MenuItemView, MenuPresentation, MenuRetailView, MenuSnapshot, OptionGroupView, QuizRef, TableView, UpsellRole,
} from "./types";
import { isRetailLine, lineName, MAX_LINE_QTY, REFILL_PREFIX, type MinimumLine, minimumShortfall, type TableMinimum } from "../pricing";
import type { CartLine } from "./cart";
import type { InvoiceDestination } from "../invoice";

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
export function presentationBadges(
  item: { presentation?: MenuPresentation; popularity?: number }, now: Date,
): string[] {
  // 人氣（docs/63 §7 M2b）照 POS 真實成交算；放在人工標籤前面。
  const badges: string[] = item.popularity === 1 ? ["人氣 No.1"] : item.popularity ? ["人氣推薦"] : [];
  const settings = item.presentation;
  if (!settings) return badges;
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

/** 品項視窗一次最多能加幾份：每項上限 10 份，今天剩不到就以剩下的為準（至少 1，售完由加入鈕擋）。 */
export function qtyLimit(item: Pick<MenuItemView, "remaining">): number {
  return Math.max(1, Math.min(MAX_LINE_QTY, item.remaining ?? MAX_LINE_QTY));
}

/** 數量的 −／＋（店主 2026-10-17）：先把客人打的數字拉回 1～上限，再加減一份。 */
export function stepQty(raw: string, delta: number, max: number): number {
  const typed = Math.floor(Number(raw));
  const current = raw.trim() !== "" && Number.isFinite(typed) ? Math.min(max, Math.max(1, typed)) : 0;
  return Math.min(max, Math.max(1, current + delta));
}

/**
 * 購物車有行不能結（售完、超量）時逐行顯示用的品名：不經過算價，下架的用替代字。
 * 體驗卡＝卡片名稱＋客人另外選的選項（與算價後的 guest_name 一致；Codex 審查 2026-10-17）。
 */
export function cartLineLabel(menu: MenuSnapshot | null, line: CartLine): string {
  if (isRetailLine(line)) return menu?.retail?.find((entry) => entry.id === line.catalog_product_id)?.name ?? "已下架商品";
  const item = menu?.items.find((entry) => entry.id === line.item_id);
  const card = line.experience_id === undefined ? undefined : menu?.experiences?.find((entry) => entry.id === line.experience_id);
  const prefix = line.refill === true ? REFILL_PREFIX : "";
  if (card !== undefined) {
    const extras = (item?.option_groups ?? []).flatMap((group) => group.options
      .filter((o) => line.option_ids.includes(o.id) && !card.option_ids.includes(o.id))
      .map((o) => ({ group: group.name, option: o.name })));
    return prefix + lineName(card.title, extras);
  }
  const selected = line.option_ids.map((id) => item?.option_groups.flatMap((group) => group.options).find((option) => option.id === id)?.name ?? "已下架選項");
  return prefix + [item?.name ?? "已下架品項", ...selected].join(" · ");
}

/** LINE Pay 付好之後告訴客人發票去哪（店主 2026-10-17）。 */
export function invoiceNote(to: InvoiceDestination): string {
  switch (to.kind) {
    case "CARRIER": return `發票會存入你輸入的手機條碼載具 ${to.carrier}。`;
    case "TAX_ID": return `統一編號 ${to.tax_id} 的發票請到櫃檯領取。`;
    case "PAPER": return "紙本發票請到櫃檯領取。";
  }
}

/**
 * 這個品項在這一桌能不能續點（店主 2026-10-19）：本桌 90 分鐘內點過（看桌不看手機）才能勾；
 * 內用還沒點過就說明要先點過；外帶、沒有桌號、不可續點的品項完全不提續點。
 */
export function refillState(
  item: Pick<MenuItemView, "id" | "refillable">,
  pct: number | null,
  table: Pick<TableView, "service_mode" | "refill_item_ids" | "refill_any"> | null,
): "ELIGIBLE" | "NOT_ORDERED" | "NONE" {
  if (item.refillable !== true || pct === null || table === null || table.service_mode !== "DINE_IN") return "NONE";
  if (table.refill_any === true) return "ELIGIBLE"; // 不限品項續點：本桌有消費過就都能續點
  return (table.refill_item_ids ?? []).includes(item.id) ? "ELIGIBLE" : "NOT_ORDERED";
}

export function tableCodeFromPath(pathname: string): string | null {
  return TABLE_PATH.exec(pathname)?.[1] ?? null;
}

/** 首頁只使用已發布的人工推薦；保留後台順序，不推測人氣或品項角色。 */
/** 有可展示品項的分類（照快照順序）。 */
function shownCategories(menu: Pick<MenuSnapshot, "categories" | "items">) {
  const items = visibleItems(menu.items);
  return menu.categories.filter((category) => items.some((item) => item.category_id === category.id));
}

export const PICKS_TAB = "picks";
export const BREW_TAB = "brew";
export const TAKEAWAY_TAB = "takeaway";
export const OTHER_TAB = "other";
export interface MenuTab { key: string; label: string; categoryId?: number }

/**
 * 完整菜單上方的分類列（店主 2026-10-10）：店員推薦（預設打開）→ 手沖體驗 → 各餐飲分類 →
 * 沒分類的「其他」→ 帶著走。沒有內容的分頁不出現。
 */
export function menuTabs(menu: MenuSnapshot): MenuTab[] {
  const tabs: MenuTab[] = [];
  if (staffPicks(menu).length) tabs.push({ key: PICKS_TAB, label: "店員推薦" });
  if ((menu.experiences ?? []).some((experience) => experienceView(menu, experience) !== null)) {
    tabs.push({ key: BREW_TAB, label: "手沖體驗" });
  }
  for (const category of shownCategories(menu)) {
    tabs.push({ key: `cat:${category.id}`, label: category.name, categoryId: category.id });
  }
  const known = new Set(menu.categories.map((category) => category.id));
  if (visibleItems(menu.items).some((item) => item.category_id === null || !known.has(item.category_id))) {
    tabs.push({ key: OTHER_TAB, label: "其他" });
  }
  if ((menu.retail ?? []).length) tabs.push({ key: TAKEAWAY_TAB, label: "帶著走" });
  return tabs;
}

export type PickView =
  | { kind: "item"; item: MenuItemView }
  | { kind: "experience"; view: ExperienceView }
  | { kind: "retail"; product: MenuRetailView };

/** 店員推薦照店主排的順序；看不到的（隱藏、已不在菜單）略過。 */
export function staffPicks(menu: MenuSnapshot): PickView[] {
  const shown = visibleItems(menu.items);
  return (menu.picks ?? []).flatMap((ref): PickView[] => {
    if (ref.kind === "item") {
      const item = shown.find((entry) => entry.id === ref.id);
      return item ? [{ kind: "item", item }] : [];
    }
    if (ref.kind === "experience") {
      const experience = (menu.experiences ?? []).find((entry) => entry.id === ref.id);
      const view = experience ? experienceView(menu, experience) : null;
      return view ? [{ kind: "experience", view }] : [];
    }
    const product = (menu.retail ?? []).find((entry) => entry.id === ref.id);
    return product ? [{ kind: "retail", product }] : [];
  });
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

/** 桌號低消的說明（顯示在購物車）；沒設＝null。 */
export function minimumNotice(minimum: TableMinimum | null): string | null {
  if (minimum === null) return null;
  return minimum.mode === "amount"
    ? `本桌低消 ${money(minimum.amount)}，每張訂單需滿 ${money(minimum.amount)} 才能送出。`
    : "本桌每位客人至少點一份餐點，請填寫用餐人數。";
}

/** 還不能送出的原因（還差多少）；可以送＝null。與雲端送單檢查用同一支計算。 */
export function minimumBlock(
  minimum: TableMinimum | null, total: number, lines: MinimumLine[], partySize: number | null,
): string | null {
  if (minimum?.mode === "per_person_item" && partySize === null) return "請先填寫用餐人數。";
  const shortfall = minimumShortfall(minimum, total, lines, partySize);
  if (shortfall === null || minimum === null) return null;
  // 續點不算低消（2026-10-16）：購物車有續點時說一聲，客人才不會對不上合計。
  const refillNote = lines.some((l) => l.refill === true) ? "（續點不計入低消）" : "";
  if (minimum.mode === "amount" && "missing_amount" in shortfall) {
    return `本桌低消 ${money(minimum.amount)}，還差 ${money(shortfall.missing_amount)}${refillNote}。`;
  }
  if (!("missing_items" in shortfall)) return null;
  return `${partySize} 位用餐，至少需點 ${partySize} 份餐點，還差 ${shortfall.missing_items} 份${refillNote}。`;
}

/** 續點折數的說法（2026-10-16）：80→「8 折」、85→「85 折」。 */
export function refillDiscountLabel(pct: number): string {
  return `${pct % 10 === 0 ? pct / 10 : pct} 折`;
}

/**
 * 送單前的低消關卡＋加點（店主 2026-10-15）：沒達低消才讓客人選「我是加點」，勾了就不擋；
 * 是不是真的加點由店員現場判斷。已達低消時不帶加點（之前勾過也一樣），POS 才不會亂標。
 */
export function minimumGate(
  minimum: TableMinimum | null, total: number, lines: MinimumLine[],
  partySize: number | null, addOnChecked: boolean,
): { block: string | null; offerAddOn: boolean; addOn: boolean } {
  const block = minimumBlock(minimum, total, lines, partySize);
  const addOn = block !== null && addOnChecked;
  return { block: addOn ? null : block, offerAddOn: block !== null, addOn };
}

/** 付款方式裡的 LINE Pay 選項：店家沒開就「即將開放」；購物車有續點要到櫃檯結帳（店主 2026-10-22）。 */
export function linePayChoice(open: boolean, hasRefill: boolean): { enabled: boolean; hint: string } {
  if (!open) return { enabled: false, hint: "即將開放" };
  if (hasRefill) return { enabled: false, hint: "續點請到櫃檯結帳" };
  return { enabled: true, hint: "現在付，付好就開始製作" };
}

/** 進購物車時的提醒（店主 2026-10-22）：有續點又開了 LINE Pay 才講，否則不打擾。 */
export function refillCounterToast(linePayOpen: boolean, hasRefill: boolean): string | null {
  return linePayOpen && hasRefill ? "有續點，請選櫃檯結帳（不能用 LINE Pay）" : null;
}
