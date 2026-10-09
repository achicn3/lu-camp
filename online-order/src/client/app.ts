// 客人掃碼點餐頁（docs/44 §4.2）。菜單文字一律 textContent，不拼 HTML。
import { drawExperience, experienceMini } from "./brew";
import { addLine, changeQty, checkCart, removeLine, type CartLine } from "./cart";
import {
  experienceView, homeSelection, greeting, itemBadge, itemSoldOut, money, presentationBadges, priceText, quizResult,
  retailGroups, retailSoldOut, tableCodeFromPath, upsellSuggestions, visibleItems, type ExperienceView,
  type UpsellPick,
} from "./logic";
import { isRetailLine } from "../pricing";
import {
  UPSELL_ROLES, type MenuItemView, type MenuRetailView, type MenuSnapshot, type TableView, type UpsellRole,
} from "./types";

const POLL_MS = 5000;
const MENU_REFRESH_MS = 15000;
const ALL = -1;
const CART_KEY = "lk_cart_v1";
const DRAFT_KEY = "lk_order_draft_v1";
const ORDER_PATH = /^\/order\/([A-Za-z0-9_-]{32,64})\/?$/;
const UPSELL_KEY = "lk_upsell_skip_v1";

interface StoreStatus { accepting: boolean; turnstile_site_key?: string | null; linepay?: boolean }
type PayMethod = "CASH" | "LINE_PAY";
interface InvoiceInput { carrier: string | null; tax_id: string | null }
const PAY_KEY = "lk_pay_v1";
const MOBILE_CARRIER = /^\/[0-9A-Z.+-]{7}$/;
const TAX_ID = /^\d{8}$/;
interface OrderLine { name: string; qty: number; line_total: number; take_home?: boolean }
interface OrderView {
  status: string; table_label: string | null; service_mode: string;
  total: number; note: string | null; created_at: string; lines: OrderLine[];
  /** 帶回家商品交貨（docs/63 §13）；舊版雲端沒有這欄。 */
  fulfillment?: "NONE" | "AWAITING" | "HANDED_OVER";
  payment_method?: PayMethod;
  /** LINE Pay 上一次沒付成的原因。 */
  linepay_result?: "CANCELLED" | "FAILED" | "EXPIRED" | null;
  /** 店內作廢／退貨後累計退了多少（舊版雲端沒有這欄）。 */
  refunded_amount?: number;
}
interface OrderCreated { token: string; status: string; total: number }
interface Draft {
  idempotency_key: string; table_code: string | null; payment_method: PayMethod; invoice?: InvoiceInput;
  note: string; lines: CartLine[]; blocked?: boolean;
}
interface TurnstileApi {
  render: (container: HTMLElement, options: { sitekey: string; callback: (token: string) => void; "expired-callback": () => void; "error-callback": () => void }) => string;
  reset: (widgetId: string) => void;
}
declare global { interface Window { turnstile?: TurnstileApi; onTurnstileReady?: () => void } }

let menu: MenuSnapshot | null = null;
let tableCode: string | null = null;
let cart: CartLine[] = [];
let status: StoreStatus | null = null;
let activeOrder: string | null = null;
let activeCategory = ALL;
let menuHome = true;
let detailTrigger: HTMLElement | null = null;
let feedbackTimer: number | null = null;
let restoringHistory = false;
let renderedMenuKey = "";
let activeDetail: number | null = null;
/** 引導推薦：null＝還沒開始；否則是已回答的答案序號（答完＝題數一樣多）。 */
let quizAnswers: number[] | null = null;
let pollTimer: number | null = null;
let widgetId: string | null = null;
let challengeToken = "";
let submitting = false;
let checkoutStarting = false;
let turnstilePromise: Promise<void> | null = null;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function $(id: string): HTMLElement {
  const node = document.getElementById(id);
  if (node === null) throw new Error(`missing #${id}`);
  return node;
}
function button(label: string, action: () => void, className = "action"): HTMLButtonElement {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}
function saveCart(): void { localStorage.setItem(CART_KEY, JSON.stringify(cart)); }
function readCart(): CartLine[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(CART_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((line): line is CartLine => typeof line === "object" && line !== null && (
      (Number.isInteger(line.catalog_product_id) && Number.isInteger(line.qty) &&
        Object.keys(line).length === 2) ||
      (Number.isInteger(line.item_id) && Number.isInteger(line.qty) && Array.isArray(line.option_ids) &&
        line.option_ids.every(Number.isInteger) &&
        (line.experience_id === undefined || Number.isInteger(line.experience_id)))));
  } catch { return []; }
}
function readDraft(): Draft | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? "null");
    if (typeof value !== "object" || value === null) return null;
    const draft = value as Partial<Draft>;
    return typeof draft.idempotency_key === "string" && /^[A-Za-z0-9_-]{16,64}$/.test(draft.idempotency_key) &&
      (draft.table_code === null || typeof draft.table_code === "string") && (draft.payment_method === "CASH" || draft.payment_method === "LINE_PAY") &&
      typeof draft.note === "string" && Array.isArray(draft.lines) ? draft as Draft : null;
  } catch { return null; }
}
async function getJson<T>(url: string): Promise<{ status: number; body: T | null }> {
  try {
    const resp = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });
    return { status: resp.status, body: resp.ok ? await resp.json() as T : null };
  } catch { return { status: 0, body: null }; }
}
async function loadHandFont(hash: string | null): Promise<void> {
  if (hash === null || !/^[0-9a-f]{64}$/.test(hash)) return;
  try {
    const face = new FontFace("LukengHand", `url(/fonts/${hash}.woff2)`);
    document.fonts.add(await face.load());
    document.documentElement.classList.add("hand-font-ready");
  } catch { /* 字型載入失敗不影響點餐 */ }
}
function showMessage(text: string): void {
  const box = $("message"); box.textContent = text; box.hidden = false;
}
function clearMessage(): void { $("message").hidden = true; }
function showScreen(screen: "menu" | "cart" | "order"): void {
  if (screen === "cart" && $("cart-view").hidden && !restoringHistory && !history.state?.cart) {
    history.pushState({ menuCategory: activeCategory, menuHome, cart: true }, "");
  }
  $("cart-feedback").textContent = "";
  $("menu-view").hidden = screen !== "menu";
  $("cart-view").hidden = screen !== "cart";
  $("order-view").hidden = screen !== "order";
  $("footer").hidden = screen !== "menu";
  window.scrollTo(0, 0);
}
function priceNode(item: MenuItemView): HTMLElement {
  const node = el("span", "item-price", money(item.unit_price));
  if (item.option_groups.length > 0) node.append(el("span", "price-from", " 起"));
  node.setAttribute("aria-label", priceText(item));
  return node;
}
function closeDetail(): void {
  const itemId = activeDetail;
  activeDetail = null;
  $("sheet").hidden = true;
  document.body.classList.remove("sheet-open");
  if (itemId === null) return;
  const fallback = document.querySelector<HTMLElement>(`${menuHome ? "#recommended-list" : "#list"} .item[data-item-id="${itemId}"] .item-detail`)
    ?? document.querySelector<HTMLElement>(menuHome ? "#shortcuts button" : "#back-home");
  (detailTrigger?.isConnected ? detailTrigger : fallback)?.focus({ preventScroll: true });
}
function openDetail(item: MenuItemView): void {
  if (menu === null) return;
  detailTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  activeDetail = item.id;
  const body = $("sheet-body"); body.replaceChildren();
  if (item.photo) {
    const img = el("img", "sheet-photo"); img.src = `/photos/${item.photo}.webp`; img.alt = item.name; body.append(img);
  }
  const title = el("h2", "sheet-title", item.name); title.id = "sheet-title"; body.append(title);
  if (item.presentation?.flavor_description) body.append(el("p", "item-flavor", item.presentation.flavor_description));
  if (item.presentation?.audience_description) body.append(el("p", "item-audience", item.presentation.audience_description));
  const labels = presentationBadges(item, new Date());
  const labelNode = el("p", "item-labels", labels.join(" · "));
  labelNode.hidden = labels.length === 0; body.append(labelNode);
  if (item.description) body.append(el("p", "sheet-desc", item.description));
  const price = priceNode(item); price.classList.add("sheet-price"); body.append(price);
  const groups = el("div");
  for (const group of item.option_groups) {
    const field = el("fieldset", "opt-group");
    const rule = group.min_select === 1 && group.max_select === 1 ? "必選 1 項" :
      group.min_select > 0 ? `選 ${group.min_select}–${group.max_select} 項` : `最多 ${group.max_select} 項`;
    field.append(el("legend", "opt-group-name", `${group.name} · ${rule}`));
    for (const option of group.options) {
      const label = el("label", "opt-choice");
      const input = el("input");
      input.type = group.max_select === 1 ? "radio" : "checkbox";
      input.name = `group-${group.id}`;
      input.value = String(option.id);
      input.disabled = !option.available || option.remaining === 0;
      label.append(input, el("span", "", option.name));
      if (option.price_delta > 0) label.append(el("span", "opt-extra", `+${money(option.price_delta)}`));
      const off = el("span", "opt-off", "售完"); off.hidden = !input.disabled; label.append(off);
      field.append(label);
    }
    groups.append(field);
  }
  body.append(groups);
  {
    const controls = el("div", "detail-controls");
    const qty = el("input", "qty-input"); qty.type = "number"; qty.min = "1"; qty.max = "10";
    qty.inputMode = "numeric"; qty.value = "1"; qty.setAttribute("aria-label", "數量");
    const error = el("p", "field-error"); error.setAttribute("role", "alert");
    const add = button("加入購物車", () => {
      if (menu === null) return;
      const optionIds = Array.from(groups.querySelectorAll<HTMLInputElement>("input:checked"), (input) => Number(input.value));
      try {
        cart = addLine(menu, cart, { item_id: item.id, option_ids: optionIds, qty: Number(qty.value) });
        saveCart(); renderFooter(); closeDetail(); clearMessage();
      } catch (reason) { error.textContent = cartError(reason); }
    });
    add.id = "detail-add"; add.disabled = itemSoldOut(item);
    controls.append(qty, add);
    body.append(controls, error);
  }
  const availability = el("p", "field-error"); availability.id = "detail-availability"; availability.setAttribute("role", "status"); availability.textContent = itemSoldOut(item) ? "今日售完，請選其他品項。" : ""; body.append(availability);
  $("sheet").hidden = false; document.body.classList.add("sheet-open"); $("sheet-close").focus();
}
function cartError(reason: unknown): string {
  const code = reason instanceof Error ? reason.message : "";
  if (code === "invalid_options") return "請依每組規則選好選項。";
  if (code === "sold_out") return "品項或選項份數不足，請調整數量。";
  if (code === "invalid_qty" || code === "too_many") return "每項最多 10 份、整張單最多 50 份及 20 項。";
  return "購物車已變動，請重新確認菜單。";
}
function itemRow(item: MenuItemView, featured = false): HTMLElement {
  const soldOut = itemSoldOut(item);
  const row = el("article", soldOut ? "item item-soldout" : "item");
  row.dataset.itemId = String(item.id);
  const detail = button("", () => openDetail(menu?.items.find((entry) => entry.id === item.id) ?? item), "item-detail");
  detail.setAttribute("aria-label", `查看${item.name}`);
  if (item.photo) {
    const photo = el("span", "item-photo");
    const img = el("img"); img.src = `/photos/${item.photo}.webp`; img.alt = "";
    img.width = 168; img.height = 168;
    img.loading = featured ? "eager" : "lazy"; img.decoding = "async"; photo.append(img); detail.append(photo);
  }
  const copy = el("span", "item-text"); copy.append(el("span", "item-name", item.name));
  if (item.presentation?.flavor_description) copy.append(el("span", "item-flavor", item.presentation.flavor_description));
  if (item.presentation?.audience_description) copy.append(el("span", "item-audience", item.presentation.audience_description));
  if (item.description && !item.presentation?.flavor_description) copy.append(el("span", "item-desc", item.description));
  const labels = presentationBadges(item, new Date());
  if (labels.length) copy.append(el("span", "item-labels", labels.join(" · ")));
  detail.append(copy); row.append(detail);
  const actions = el("div", "item-actions");
  const price = el("div"); price.append(priceNode(item));
  const badge = soldOut ? "今日售完" : itemBadge(item);
  if (badge) price.append(el("span", soldOut ? "item-badge item-badge-off" : "item-badge", badge));
  const add = button(soldOut ? "今日售完" : item.option_groups.length ? "選擇選項" : "加入", () => {
    const current = menu?.items.find((entry) => entry.id === item.id);
    if (!menu || !current) { showMessage("菜單已更新，請重新選擇。"); return; }
    if (current.option_groups.length) { openDetail(current); return; }
    try {
      cart = addLine(menu, cart, { item_id: current.id, option_ids: [], qty: 1 });
      saveCart(); renderFooter(); clearMessage();
      $("cart-feedback").textContent = `已加入${current.name}`;
      if (feedbackTimer !== null) clearTimeout(feedbackTimer);
      feedbackTimer = window.setTimeout(() => { $("cart-feedback").textContent = ""; }, 2500);
    } catch (reason) { showMessage(cartError(reason)); }
  }, "item-add");
  add.disabled = soldOut; add.setAttribute("aria-label", `${soldOut ? "今日售完" : item.option_groups.length ? "選擇選項" : "加入"}：${item.name}`);
  actions.append(price, add); row.append(actions);
  return row;
}
const RETAIL_LOW = 5;
/** 帶回家商品一列：照片、名稱、介紹、價格、剩幾件（≤5 才顯示）、加入。 */
function retailRow(product: MenuRetailView): HTMLElement {
  const soldOut = retailSoldOut(product);
  const row = el("article", soldOut ? "item item-soldout" : "item");
  row.dataset.productId = String(product.id);
  const detail = el("div", "item-detail");
  if (product.photo) {
    const photo = el("span", "item-photo");
    const img = el("img"); img.src = `/photos/${product.photo}.webp`; img.alt = "";
    img.width = 168; img.height = 168; img.loading = "lazy"; img.decoding = "async";
    photo.append(img); detail.append(photo);
  }
  const copy = el("span", "item-text"); copy.append(el("span", "item-name", product.name));
  if (product.description) copy.append(el("span", "item-desc", product.description));
  detail.append(copy); row.append(detail);
  const actions = el("div", "item-actions");
  const price = el("div"); price.append(el("span", "item-price", money(product.unit_price)));
  const badge = soldOut ? "售完" : product.remaining <= RETAIL_LOW ? `剩 ${product.remaining} 件` : null;
  if (badge) price.append(el("span", soldOut ? "item-badge item-badge-off" : "item-badge", badge));
  const add = button(soldOut ? "售完" : "加入", () => {
    if (menu === null) return;
    try {
      cart = addLine(menu, cart, { catalog_product_id: product.id, qty: 1 });
      saveCart(); renderFooter(); clearMessage();
      $("cart-feedback").textContent = `已加入${product.name}`;
      if (feedbackTimer !== null) clearTimeout(feedbackTimer);
      feedbackTimer = window.setTimeout(() => { $("cart-feedback").textContent = ""; }, 2500);
    } catch (reason) { showMessage(cartError(reason)); }
  }, "item-add");
  add.disabled = soldOut; add.setAttribute("aria-label", `${soldOut ? "售完" : "加入"}：${product.name}`);
  actions.append(price, add); row.append(actions);
  return row;
}
function selectCategory(category: number, home = false, push = true): void {
  activeCategory = category; menuHome = home;
  if (push) history.pushState({ menuCategory: category, menuHome: home }, "");
  if (menu) renderMenu(menu);
  window.scrollTo(0, 0);
}
function renderMenu(snapshot: MenuSnapshot): void {
  const key = JSON.stringify([snapshot, menuHome, activeCategory, new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei" }).format(new Date())]);
  if (key === renderedMenuKey) return;
  renderedMenuKey = key;
  const tabs = $("tabs"); const list = $("list");
  const selection = homeSelection(snapshot);
  if (!selection.categories.some((category) => category.id === activeCategory)) activeCategory = ALL;
  $("menu-home").hidden = !menuHome; $("back-home").hidden = menuHome;
  $("catalog-title").textContent = menuHome ? "完整菜單" : selection.categories.find((category) => category.id === activeCategory)?.name ?? "全部品項";
  const categories = [{ id: ALL, name: "全部" }, ...selection.categories];
  tabs.replaceChildren(...categories.map((category) => {
    const tab = button(category.name, () => selectCategory(category.id), !menuHome && category.id === activeCategory ? "tab tab-on" : "tab");
    tab.setAttribute("aria-pressed", String(!menuHome && category.id === activeCategory)); return tab;
  }));
  tabs.hidden = false;
  list.replaceChildren(...(menuHome ? [] : visibleItems(snapshot.items).filter((item) => activeCategory === ALL || item.category_id === activeCategory).map((item) => itemRow(item))));
  if (!menuHome && !list.childElementCount) list.append(el("p", "empty-state", "菜單準備中，請洽櫃台點餐。"));
  const experiences = (snapshot.experiences ?? [])
    .map((experience) => experienceView(snapshot, experience))
    .filter((view): view is ExperienceView => view !== null);
  $("experiences").hidden = experiences.length === 0;
  $("experience-list").replaceChildren(...(menuHome ? experiences.map((view) => experienceMini(view, (from) => openExperience(view, from))) : []));
  const takeHome = retailGroups(snapshot);
  $("take-home").hidden = takeHome.length === 0;
  $("take-home-list").replaceChildren(...(menuHome ? takeHome.map((group) => {
    const box = el("div", "take-home-group");
    box.append(el("h3", "take-home-category", group.category), ...group.products.map(retailRow));
    return box;
  }) : []));
  $("recommendations").hidden = selection.recommended.length === 0;
  $("recommended-list").replaceChildren(...(menuHome ? selection.recommended.map((item) => itemRow(item, true)) : []));
  renderQuiz(snapshot);
  const shortcuts: HTMLElement[] = [];
  if (snapshot.quiz) shortcuts.push(button("不知道喝什麼？", () => startQuiz(), "shortcut"));
  if (experiences.length) shortcuts.push(button("手沖體驗", () => $("experiences").scrollIntoView({ block: "start" }), "shortcut"));
  if (selection.recommended.length) shortcuts.push(button("露坑推薦", () => $("recommendations").scrollIntoView({ block: "start" }), "shortcut"));
  const preferred = [selection.categories.find((category) => category.name === "咖啡"),
    selection.categories.find((category) => category.name === "甜點") ?? selection.categories.find((category) => category.name === "今日甜點"),
    ...selection.categories];
  const chosen = new Set<number>();
  for (const category of preferred) {
    if (!category || chosen.has(category.id)) continue;
    chosen.add(category.id); shortcuts.push(button(category.name, () => selectCategory(category.id), "shortcut"));
    if (chosen.size === 2) break;
  }
  if (takeHome.length) shortcuts.push(button("帶回家", () => $("take-home").scrollIntoView({ block: "start" }), "shortcut"));
  if (!shortcuts.length) shortcuts.push(button("看看菜單", () => selectCategory(ALL), "shortcut"));
  $("shortcuts").replaceChildren(...shortcuts);
}
function startQuiz(): void {
  quizAnswers = [];
  if (menu) renderQuiz(menu);
  $("quiz").scrollIntoView({ block: "start" });
}
function answerQuiz(answer: number | null): void {
  if (quizAnswers === null) return;
  quizAnswers = answer === null ? quizAnswers.slice(0, -1) : [...quizAnswers, answer];
  if (menu) renderQuiz(menu);
  // 換題／出結果後把問答區頂端帶回畫面（不被上方固定列擋住）。
  $("quiz").scrollIntoView({ block: "start" });
}
/** 「不知道喝什麼」（docs/63 §2 M2a）：一題一題問，答完給 1 主推＋最多 2 備選；對不上就給完整菜單。 */
function renderQuiz(snapshot: MenuSnapshot): void {
  const quiz = snapshot.quiz;
  $("quiz").hidden = !menuHome || quiz === undefined;
  const body = $("quiz-body");
  if (quiz === undefined) { body.replaceChildren(); return; }
  if (quizAnswers === null) {
    body.replaceChildren(button("幫我挑", startQuiz, "action quiz-start"));
    return;
  }
  const step = quizAnswers.length;
  const question = quiz.questions[step];
  if (question !== undefined) {
    const options = el("div", "quiz-options");
    options.append(...question.options.map((option, index) => button(option.label, () => answerQuiz(index), "quiz-option")));
    const nav = el("div", "quiz-nav");
    nav.append(button(step === 0 ? "先不用" : "上一題", () => {
      if (step === 0) { quizAnswers = null; renderQuiz(snapshot); } else answerQuiz(null);
    }, "link-button"));
    body.replaceChildren(el("p", "quiz-step", `第 ${step + 1} / ${quiz.questions.length} 題`),
      el("h3", "quiz-prompt", question.prompt), options, nav);
    return;
  }
  const picks = quizResult(snapshot, quizAnswers);
  const again = button("重新回答", startQuiz, "link-button");
  if (picks.length === 0) {
    body.replaceChildren(el("p", "quiz-empty", "今天沒有剛好符合的，看看完整菜單吧。"),
      button("看完整菜單", () => selectCategory(ALL), "action"), again);
    return;
  }
  const rows = picks.flatMap((ref, index): HTMLElement[] => {
    let node: HTMLElement | null = null;
    if (ref.kind === "item") {
      const found = snapshot.items.find((entry) => entry.id === ref.id);
      node = found ? itemRow(found, true) : null;
    } else {
      const experience = (snapshot.experiences ?? []).find((entry) => entry.id === ref.id);
      const view = experience ? experienceView(snapshot, experience) : null;
      node = view ? experienceMini(view, (from) => openExperience(view, from)) : null;
    }
    if (node === null) return [];
    return index === 0 ? [el("p", "quiz-label", "最推薦"), node] : index === 1 ? [el("p", "quiz-label", "也可以試試"), node] : [node];
  });
  body.replaceChildren(...rows, again);
}
function renderFooter(): void {
  const footer = $("footer"); footer.replaceChildren();
  const qty = cart.reduce((sum, line) => sum + line.qty, 0);
  footer.append(button(qty ? `購物車 ${qty} 份 · 查看` : "查看購物車", renderCart, "footer-action"));
}
function renderCart(): void {
  if (menu === null) return;
  const body = $("cart-body"); body.replaceChildren(); clearMessage();
  const draft = readDraft();
  if (draft !== null) {
    body.append(el("p", "message", draft.blocked
      ? "這筆訂單的識別碼有衝突。請到櫃台確認，暫時不要重新送單。"
      : "上一筆訂單的結果尚未確認。請用同一筆資料重試，避免重複送單。"));
    if (!draft.blocked) body.append(button("重試確認訂單", () => void submitDraft(draft)));
    showScreen("cart"); return;
  }
  if (!cart.length) {
    body.append(el("p", "empty-state", "購物車還是空的，回菜單挑喜歡的品項吧。"));
    showScreen("cart"); return;
  }
  const priced = checkCart(menu, cart);
  if (!priced.ok) {
    body.append(el("p", "field-error", cartError(new Error(priced.reason))));
    cart.forEach((line, index) => {
      const row = el("div", "cart-line");
      let name: string;
      if (isRetailLine(line)) {
        name = menu?.retail?.find((entry) => entry.id === line.catalog_product_id)?.name ?? "已下架商品";
      } else {
        const item = menu?.items.find((entry) => entry.id === line.item_id);
        const selected = line.option_ids.map((id) => item?.option_groups.flatMap((group) => group.options).find((option) => option.id === id)?.name ?? "已下架選項");
        name = [item?.name ?? "已下架品項", ...selected].join(" · ");
      }
      const info = el("div"); info.append(el("b", "", name), el("span", "", `${line.qty} 份`));
      const checked = menu ? checkCart(menu, [line]) : null;
      if (checked && !checked.ok) info.append(el("span", "field-error", cartError(new Error(checked.reason))));
      const controls = el("div", "cart-controls");
      controls.append(button("−", () => {
        cart = line.qty <= 1 ? removeLine(cart, index) : cart.map((entry, i) => i === index ? { ...entry, qty: entry.qty - 1 } : entry);
        saveCart(); renderFooter(); renderCart();
      }, "qty-button"), button("移除", () => { cart = removeLine(cart, index); saveCart(); renderFooter(); renderCart(); }, "quiet-action"));
      row.append(info, controls); body.append(row);
    });
    showScreen("cart"); return;
  }
  priced.lines.forEach((line, index) => {
    const row = el("div", "cart-line");
    const info = el("div"); info.append(el("b", "", line.name), el("span", "item-price", money(line.line_total)));
    const controls = el("div", "cart-controls");
    controls.append(button("−", () => updateCartQty(index, line.qty - 1), "qty-button"),
      el("span", "", String(line.qty)),
      button("+", () => updateCartQty(index, line.qty + 1), "qty-button"),
      button("移除", () => { cart = removeLine(cart, index); saveCart(); renderFooter(); renderCart(); }, "quiet-action"));
    row.append(info, controls); body.append(row);
  });
  body.append(el("p", "cart-total", `合計 ${money(priced.total)}`));
  const upsell = upsellBlock();
  if (upsell) body.append(upsell);
  const noteLabel = el("label", "note-label", "備註（最多 60 字）");
  const note = el("textarea") as HTMLTextAreaElement; note.id = "order-note"; note.maxLength = 60;
  note.rows = 2; note.value = sessionStorage.getItem("lk_note") ?? "";
  note.addEventListener("input", () => sessionStorage.setItem("lk_note", note.value));
  noteLabel.append(note); body.append(noteLabel);
  const payment = paymentChoice();
  body.append(payment.node);
  if (cart.some(isRetailLine)) body.append(el("p", "take-home-note", "帶回家商品請到櫃檯領取。"));
  const submit = button(payment.method() === "LINE_PAY" ? "用 LINE Pay 付款" : "送出現金訂單", () => {
    const invoice = payment.invoice();
    if (invoice === null) { showMessage("手機條碼是 / 開頭共 8 碼；統一編號是 8 位數字。"); return; }
    void startCheckout(note.value, payment.method(), invoice);
  });
  payment.onChange(() => { submit.textContent = payment.method() === "LINE_PAY" ? "用 LINE Pay 付款" : "送出現金訂單"; });
  body.append(submit);
  showScreen("cart");
}
/** 付款方式（docs/44 §4.4.2）：店家有開 LINE Pay 才能選；LINE Pay 可填手機條碼或統編，沒填印紙本。 */
function paymentChoice(): {
  node: HTMLElement; method: () => PayMethod; invoice: () => InvoiceInput | null; onChange: (fn: () => void) => void;
} {
  const box = el("div", "pay-choice");
  if (!status?.linepay) {
    box.append(el("p", "payment-note", "付款方式：現金 · 到櫃台付款後才會製作。"));
    return { node: box, method: () => "CASH", invoice: () => ({ carrier: null, tax_id: null }), onChange: () => undefined };
  }
  let method: PayMethod = sessionStorage.getItem(PAY_KEY) === "LINE_PAY" ? "LINE_PAY" : "CASH";
  const listeners: (() => void)[] = [];
  const group = el("fieldset", "pay-methods"); group.append(el("legend", "", "付款方式"));
  const extra = el("div", "invoice-fields");
  const kind = el("select") as HTMLSelectElement; kind.id = "invoice-kind"; kind.setAttribute("aria-label", "發票");
  for (const [value, label] of [["paper", "紙本（到櫃檯拿）"], ["carrier", "手機條碼載具"], ["tax", "統一編號"]]) {
    const option = el("option", "", label) as HTMLOptionElement; option.value = value!; kind.append(option);
  }
  const code = el("input") as HTMLInputElement; code.id = "invoice-code"; code.autocomplete = "off"; code.hidden = true;
  kind.addEventListener("change", () => {
    code.hidden = kind.value === "paper"; code.value = "";
    code.placeholder = kind.value === "carrier" ? "/ABC+123" : "12345678";
    code.inputMode = kind.value === "tax" ? "numeric" : "text";
    code.setAttribute("aria-label", kind.value === "carrier" ? "手機條碼" : "統一編號");
  });
  const kindLabel = el("label", "note-label", "發票"); kindLabel.append(kind);
  extra.append(kindLabel, code);
  for (const [value, label, hint] of [["CASH", "現金", "到櫃台付款後才會製作"], ["LINE_PAY", "LINE Pay", "現在付，付好就開始製作"]] as const) {
    const option = el("label", "pay-option");
    const radio = el("input") as HTMLInputElement; radio.type = "radio"; radio.name = "pay-method"; radio.value = value;
    radio.checked = method === value;
    radio.addEventListener("change", () => {
      method = value; sessionStorage.setItem(PAY_KEY, value); extra.hidden = value !== "LINE_PAY";
      listeners.forEach((fn) => fn());
    });
    const text = el("span"); text.append(el("b", "", label), el("small", "", hint));
    option.append(radio, text); group.append(option);
  }
  extra.hidden = method !== "LINE_PAY";
  box.append(group, extra);
  return {
    node: box,
    method: () => method,
    invoice: () => {
      if (method !== "LINE_PAY" || kind.value === "paper") return { carrier: null, tax_id: null };
      const value = code.value.trim().toUpperCase();
      if (kind.value === "carrier") return MOBILE_CARRIER.test(value) ? { carrier: value, tax_id: null } : null;
      return TAX_ID.test(value) ? { carrier: null, tax_id: value } : null;
    },
    onChange: (fn) => listeners.push(fn),
  };
}
function skippedUpsell(): Set<UpsellRole> {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(UPSELL_KEY) ?? "[]");
    return new Set(Array.isArray(parsed) ? parsed.filter((role): role is UpsellRole => UPSELL_ROLES.includes(role)) : []);
  } catch { return new Set(); }
}
const UPSELL_COPY: Partial<Record<UpsellRole, string>> = { dessert: "配個甜的？", coffee: "要不要來杯咖啡？" };
/** 購物車下方的「配個…？」（docs/63 §6）：不跳視窗、不自動加入、不打折；略過就不再推同一類。 */
function upsellBlock(): HTMLElement | null {
  if (menu === null) return null;
  const picks = upsellSuggestions(menu, cart, skippedUpsell());
  if (!picks.length) return null;
  const roles = [...new Set(picks.map((pick) => pick.role))];
  const box = el("section", "upsell");
  box.setAttribute("aria-label", "加購推薦");
  box.append(el("p", "upsell-title", roles.length === 1 ? UPSELL_COPY[roles[0]!] ?? "要不要再帶一個？" : "要不要再帶一個？"));
  for (const pick of picks) box.append(upsellRow(pick));
  box.append(button("不用了", () => {
    const skipped = skippedUpsell();
    roles.forEach((role) => skipped.add(role));
    sessionStorage.setItem(UPSELL_KEY, JSON.stringify([...skipped]));
    box.remove();
  }, "upsell-skip"));
  return box;
}
function upsellRow(pick: UpsellPick): HTMLElement {
  const row = el("div", "upsell-item");
  const info = el("div");
  const options = pick.kind === "menu" && pick.option_groups.length > 0;
  info.append(el("b", "", pick.name),
    el("span", "item-price", ` ${pick.kind === "menu" ? priceText(pick) : money(pick.unit_price)}`));
  row.append(info, button(options ? "選擇選項" : "一起帶", () => {
    if (menu === null) return;
    if (pick.kind === "menu" && options) { openDetail(pick); return; }
    const line: CartLine = pick.kind === "menu"
      ? { item_id: pick.id, option_ids: [], qty: 1 }
      : { catalog_product_id: pick.id, qty: 1 };
    try { cart = addLine(menu, cart, line); saveCart(); renderFooter(); renderCart(); }
    catch (reason) { showMessage(cartError(reason)); }
  }, "item-add"));
  return row;
}
function openExperience(view: ExperienceView, from: HTMLElement): void {
  void drawExperience(view, from, {
    onAdd: (optionIds, qty) => {
      if (menu === null) return "菜單已更新，請重新選擇。";
      try {
        cart = addLine(menu, cart, { item_id: view.item.id, option_ids: optionIds, qty, experience_id: view.experience.id });
      } catch (reason) {
        const code = reason instanceof Error ? reason.message : "";
        return code === "invalid_options" ? "請先選好下面的選項。" : cartError(reason);
      }
      saveCart(); renderFooter(); clearMessage();
      $("cart-feedback").textContent = `已加入${view.experience.title}`;
      if (feedbackTimer !== null) clearTimeout(feedbackTimer);
      feedbackTimer = window.setTimeout(() => { $("cart-feedback").textContent = ""; }, 2500);
      return null;
    },
    onClose: () => undefined,
  });
}
function updateCartQty(index: number, qty: number): void {
  if (menu === null) return;
  try { cart = changeQty(menu, cart, index, qty); saveCart(); renderFooter(); renderCart(); }
  catch (reason) { showMessage(cartError(reason)); }
}
function loadTurnstile(): Promise<void> {
  if (typeof window.turnstile?.render === "function") return Promise.resolve();
  if (turnstilePromise !== null) return turnstilePromise;
  turnstilePromise = new Promise((resolve, reject) => {
    const script = document.createElement("script"); script.dataset.turnstile = "true";
    const timer = window.setTimeout(() => fail(), 15000);
    const fail = () => {
      clearTimeout(timer); script.remove(); window.onTurnstileReady = undefined;
      turnstilePromise = null; reject(new Error("challenge_unavailable"));
    };
    window.onTurnstileReady = () => { clearTimeout(timer); resolve(); };
    script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=onTurnstileReady";
    script.async = true; script.onerror = fail;
    document.head.append(script);
  });
  return turnstilePromise;
}
async function challenge(): Promise<string> {
  const sitekey = status?.turnstile_site_key;
  if (!sitekey) throw new Error("challenge_unavailable");
  await loadTurnstile();
  const api = window.turnstile;
  if (typeof api?.render !== "function") throw new Error("challenge_unavailable");
  challengeToken = "";
  if (widgetId !== null) api.reset(widgetId);
  else widgetId = api.render($("challenge-widget"), {
    sitekey, callback: (token) => { challengeToken = token; },
    "expired-callback": () => { challengeToken = ""; },
    "error-callback": () => { challengeToken = ""; },
  });
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const timer = window.setInterval(() => {
      if (challengeToken) { clearInterval(timer); resolve(challengeToken); }
      else if (Date.now() - started > 120000) { clearInterval(timer); reject(new Error("challenge_unavailable")); }
    }, 200);
  });
}
function newDraft(note: string): Draft {
  return { idempotency_key: crypto.randomUUID(), table_code: tableCode, payment_method: "CASH",
    note: note.trim(), lines: cart.map((line) => isRetailLine(line) ? { ...line } : { ...line, option_ids: [...line.option_ids] }) };
}
async function startCheckout(note: string, method: PayMethod = "CASH", invoice: InvoiceInput = { carrier: null, tax_id: null }): Promise<void> {
  if (menu === null || checkoutStarting || submitting) return;
  checkoutStarting = true;
  try {
  const priced = checkCart(menu, cart);
  if (!priced.ok) { showMessage(cartError(new Error(priced.reason))); return; }
  if ([...note].length > 60) { showMessage("備註最多 60 字。"); return; }
  const live = await getJson<StoreStatus>("/api/status"); status = live.body;
  if (!status?.accepting) { showMessage("目前暫停接單，請到櫃台點餐。"); return; }
  if (!status.turnstile_site_key) { showMessage("驗證服務尚未設定，請到櫃台點餐。"); return; }
  if (method === "LINE_PAY" && !status.linepay) { showMessage("LINE Pay 暫時不能用，請改用現金。"); return; }
  const draft = { ...newDraft(note), payment_method: method, invoice };
  localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  await submitDraft(draft);
  } finally { checkoutStarting = false; }
}
async function submitDraft(draft: Draft): Promise<void> {
  if (submitting || draft.blocked) return;
  submitting = true;
  const body = $("cart-body");
  body.replaceChildren(el("p", "message", "正在確認訂單，請完成安全驗證。"));
  showScreen("cart");
  try {
    if (!status?.turnstile_site_key) status = (await getJson<StoreStatus>("/api/status")).body;
    const token = await challenge();
    body.querySelector(".message")!.textContent = "正在送出訂單…";
    const resp = await fetch("/api/orders", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ idempotency_key: draft.idempotency_key, table_code: draft.table_code,
        payment_method: draft.payment_method, note: draft.note, lines: draft.lines, turnstile_token: token,
        ...(draft.payment_method === "LINE_PAY" && draft.invoice ? { invoice: draft.invoice } : {}) }) });
    if (resp.ok) {
      const created = await resp.json() as OrderCreated;
      localStorage.removeItem(DRAFT_KEY); localStorage.removeItem(CART_KEY); sessionStorage.removeItem("lk_note");
      cart = []; renderFooter(); history.pushState(null, "", `/order/${created.token}`);
      // LINE Pay：不用等保留的單直接去付款；要等 POS 確認限量的，留在訂單頁等「前往付款」。
      if (draft.payment_method === "LINE_PAY" && created.status !== "HOLD_REQUESTED" && await goLinePay(created.token)) return;
      await openOrder(created.token); return;
    }
    const result = await resp.json() as { error?: string };
    if (resp.status === 409) {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ ...draft, blocked: true }));
      renderCart(); showMessage("訂單識別碼有衝突。請到櫃台確認，暫時不要重新送單。");
    } else if (resp.status === 422 || resp.status === 403 || resp.status === 404) {
      localStorage.removeItem(DRAFT_KEY);
      renderCart();
      showMessage(result.error === "sold_out" ? "品項已售完，請更新菜單後再選。" :
        result.error === "challenge_failed" ? "驗證未通過，請重新送出。" : "訂單內容有變，請重新確認後送出。");
    } else {
      renderCart(); showMessage("尚未確認訂單是否成立。請按「重試確認訂單」，不要重新選品項送單。");
    }
  } catch {
    renderCart(); showMessage("連線中斷，尚未確認訂單是否成立。請按「重試確認訂單」。");
  } finally { submitting = false; challengeToken = ""; }
}
/** 向雲端要 LINE Pay 付款連結並跳過去；失敗回 false（留在訂單頁顯示原因）。 */
async function goLinePay(token: string): Promise<boolean> {
  try {
    const resp = await fetch(`/api/orders/${token}/linepay`, { method: "POST", headers: { Accept: "application/json" } });
    if (!resp.ok) {
      const result = await resp.json() as { error?: string };
      showMessage(result.error === "hold_pending" ? "正在確認限量品項，確認後就能付款。"
        : result.error === "hold_expired" ? "保留時間已過，請回菜單重新點。" : "暫時連不上 LINE Pay，請稍後再試或到櫃檯付現金。");
      return false;
    }
    const { payment_url } = await resp.json() as { payment_url: string };
    location.assign(payment_url);
    return true;
  } catch {
    showMessage("連線中斷，請稍後再試。"); return false;
  }
}
/** 從 LINE Pay 導回訂單頁：付好了就請款、取消就記取消，然後把網址上的參數拿掉。 */
async function settleLinePayReturn(token: string): Promise<void> {
  const mode = new URLSearchParams(location.search).get("linepay");
  if (mode !== "return" && mode !== "cancel") return;
  history.replaceState(history.state, "", `/order/${token}`);
  try {
    await fetch(`/api/orders/${token}/linepay/${mode === "return" ? "confirm" : "cancel"}`, { method: "POST" });
  } catch { /* 請款結果不明：雲端會補查，訂單頁照常更新 */ }
}
function linePayState(view: OrderView): string | null {
  if (view.payment_method !== "LINE_PAY") return null;
  switch (view.status) {
    case "PENDING": return "等待 LINE Pay 付款。如果已經付了，請稍候確認。";
    case "CONFIRMING": return "付款確認中，請稍候，不要重複付款。";
    case "HOLD_REQUESTED": return "訂單已收到，正在確認限量品項，確認後就能用 LINE Pay 付款。";
    case "UNPAID":
      if (view.linepay_result === "EXPIRED") return "保留時間已過、沒有扣款。請回菜單重新點。";
      if (view.linepay_result === "CANCELLED") return "LINE Pay 付款已取消，沒有扣款。可以重新付款，或到櫃台付現金。";
      if (view.linepay_result === "FAILED") return "LINE Pay 付款沒有成功，沒有扣款。可以重新付款，或到櫃台付現金。";
      return "訂單已收到，請用 LINE Pay 付款，或到櫃台付現金。";
    case "PAID": return "LINE Pay 已付款，開始製作囉。";
    default: return null;
  }
}
function orderState(view: OrderView): string {
  // 店內退了款就以退款為準（作廢後帶回家商品也不會再交）。
  const amount = view.refunded_amount ? ` ${money(view.refunded_amount)}` : "";
  if (view.status === "REFUNDED") return `這張訂單已退款${amount}。`;
  if (view.status === "PARTIALLY_REFUNDED") return `這張訂單已部分退款${amount}。`;
  if (view.fulfillment === "HANDED_OVER") return "已領取，謝謝你。";
  if (view.fulfillment === "AWAITING") return "已付款。帶回家商品請到櫃檯領取。";
  const linePay = linePayState(view);
  if (linePay !== null) return linePay;
  switch (view.status) {
    case "HOLD_REQUESTED": return "訂單已收到，正在確認限量品項。請稍候。";
    case "HELD": return "訂單已確認。請到櫃台付現金，付款後才會製作。";
    case "UNPAID": return "訂單已收到。請到櫃台付現金，付款後才會製作。";
    case "PAID": return "已付款，謝謝你。";
    case "REJECTED": return "份數不足，這張訂單未成立。請回菜單重新選。";
    case "CANCELLED": return "這張訂單已取消。";
    default: return "正在確認訂單狀態。";
  }
}
async function refreshOrder(): Promise<void> {
  if (activeOrder === null) return;
  const result = await getJson<OrderView>(`/api/orders/${activeOrder}`);
  const body = $("order-body");
  if (!result.body) { body.replaceChildren(el("p", "message", "暫時查不到訂單，請稍後再試。")); return; }
  const view = result.body; body.replaceChildren();
  body.append(el("p", "order-state", orderState(view)));
  if (view.table_label) body.append(el("p", "", `桌號 ${view.table_label}`));
  else body.append(el("p", "", "外帶"));
  for (const line of view.lines) {
    body.append(el("p", "order-line", `${line.name} × ${line.qty}　${money(line.line_total)}${line.take_home ? "　（帶回家）" : ""}`));
  }
  if (view.lines.some((line) => line.take_home) && view.fulfillment !== "HANDED_OVER" && view.status !== "REFUNDED") {
    body.append(el("p", "take-home-note", "帶回家商品請到櫃檯領取。"));
  }
  body.append(el("p", "cart-total", `合計 ${money(view.total)}`));
  if (view.note) body.append(el("p", "", `備註：${view.note}`));
  const canPay = view.payment_method === "LINE_PAY" && (view.status === "UNPAID" || view.status === "PENDING") &&
    view.linepay_result !== "EXPIRED";
  if (canPay && activeOrder !== null) {
    const token = activeOrder;
    body.append(button(view.status === "PENDING" ? "前往 LINE Pay 付款" : "用 LINE Pay 付款", () => void goLinePay(token)));
  }
  body.append(el("p", "order-hint", "可儲存此頁網址，稍後查看付款狀態。"));
  if (view.status === "REJECTED" || view.status === "CANCELLED")
    body.append(button("返回菜單", () => { history.pushState(null, "", tableCode ? `/t/${tableCode}` : "/"); activeOrder = null; showScreen("menu"); }));
  // 付了錢但帶回家商品還沒交：繼續更新，交貨後客人頁才會變「已領取」。LINE Pay 付款中也繼續更新。
  const done = view.status === "REFUNDED" ||
    (["PAID", "REJECTED", "CANCELLED", "PARTIALLY_REFUNDED"].includes(view.status) && view.fulfillment !== "AWAITING");
  if (done && pollTimer !== null) {
    clearInterval(pollTimer); pollTimer = null;
  }
}
async function refreshAvailability(): Promise<void> {
  if (activeOrder !== null || menu === null) return;
  const [nextMenu, nextStatus] = await Promise.all([
    getJson<MenuSnapshot>("/api/menu"), getJson<StoreStatus>("/api/status"),
  ]);
  if (nextStatus.body) status = nextStatus.body;
  if (nextMenu.body && JSON.stringify(nextMenu.body) !== JSON.stringify(menu)) {
    menu = nextMenu.body;
    const current = checkCart(menu, cart);
    if (!$("cart-view").hidden && readDraft() === null && !submitting && !checkoutStarting) renderCart();
    if (cart.length && !current.ok) showMessage("菜單供應狀態已更新，請檢查購物車。");
  }
  renderMenu(menu); // 限定標籤跨台北午夜失效，無須重發快照。
  if (activeDetail !== null) {
    const item = menu.items.find((entry) => entry.id === activeDetail);
    const unavailable = !item || itemSoldOut(item);
    const add = document.getElementById("detail-add") as HTMLButtonElement | null;
    if (add) add.disabled = unavailable;
    let invalidSelection = false;
    for (const input of $("sheet-body").querySelectorAll<HTMLInputElement>(".opt-choice input")) {
      const option = item?.option_groups.flatMap((group) => group.options).find((entry) => entry.id === Number(input.value));
      input.disabled = !option || !option.available || option.remaining === 0;
      const off = input.parentElement?.querySelector<HTMLElement>(".opt-off");
      if (off) off.hidden = !input.disabled;
      if (input.disabled && input.checked) { input.checked = false; invalidSelection = true; }
    }
    $("detail-availability").textContent = unavailable ? "這個品項剛剛售完了，請選其他品項。" : invalidSelection ? "選項供應已更新，請重新選擇。" : "";
    const node = $("sheet-body").querySelector<HTMLElement>(".item-labels");
    if (node) {
      const labels = item ? presentationBadges(item, new Date()) : [];
      node.textContent = labels.join(" · "); node.hidden = labels.length === 0;
    }
  }
}
async function openOrder(token: string): Promise<void> {
  activeOrder = token; showScreen("order");
  $("order-body").replaceChildren(el("p", "", "正在載入訂單…"));
  await settleLinePayReturn(token);
  if (pollTimer !== null) clearInterval(pollTimer);
  pollTimer = window.setInterval(() => void refreshOrder(), POLL_MS);
  await refreshOrder();
}
async function main(): Promise<void> {
  $("greeting").textContent = greeting(new Date());
  $("sheet-close").addEventListener("click", closeDetail);
  $("sheet").addEventListener("click", (event) => { if (event.target === event.currentTarget) closeDetail(); });
  document.addEventListener("keydown", (event) => {
    if ($("sheet").hidden) return;
    if (event.key === "Escape") { closeDetail(); return; }
    if (event.key !== "Tab") return;
    const controls = Array.from($("sheet").querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled)"));
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });
  $("back-menu").addEventListener("click", () => {
    if (history.state?.cart) history.back();
    else showScreen("menu");
  });
  $("back-home").addEventListener("click", () => selectCategory(ALL, true));
  // 付款完成後就不再輪詢；客人切回這個分頁時重抓一次，店裡後來退了款也看得到（docs/44 §4.5 C6）。
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && activeOrder !== null) void refreshOrder();
  });
  window.addEventListener("popstate", (event: PopStateEvent) => {
    if (ORDER_PATH.test(location.pathname) || activeOrder) { location.reload(); return; }
    restoringHistory = true;
    closeDetail();
    selectCategory(event.state?.menuCategory ?? ALL, event.state?.menuHome ?? true, false);
    if (event.state?.cart) renderCart(); else showScreen("menu");
    restoringHistory = false;
  });
  if (!ORDER_PATH.test(location.pathname)) {
    menuHome = history.state?.menuHome ?? true; activeCategory = history.state?.menuCategory ?? ALL;
    history.replaceState({ menuCategory: activeCategory, menuHome, cart: history.state?.cart === true }, "");
  }
  tableCode = tableCodeFromPath(location.pathname);
  const orderToken = ORDER_PATH.exec(location.pathname)?.[1];
  const [menuResult, tableResult, storeResult] = await Promise.all([
    getJson<MenuSnapshot>("/api/menu"),
    tableCode ? getJson<TableView>(`/api/tables/${tableCode}`) : Promise.resolve(null),
    getJson<StoreStatus>("/api/status"),
  ]);
  status = storeResult.body;
  if (tableResult !== null) {
    if (tableResult.body) {
      $("table").textContent = tableResult.body.service_mode === "TAKEOUT" ? "外帶" : `桌 ${tableResult.body.label}`;
      $("table").hidden = false;
    } else if (tableResult.status === 404) { showMessage("這個 QR 已經失效了，請洽櫃台。"); return; }
  }
  if (menuResult.body === null) {
    if (orderToken) { await openOrder(orderToken); return; }
    showMessage(menuResult.status === 404 ? "菜單準備中，請洽櫃台點餐。" : "連不上網路，請稍後再試或至櫃台點餐。"); return;
  }
  menu = menuResult.body; void loadHandFont(menu.font);
  cart = readCart(); renderMenu(menu); renderFooter();
  if (orderToken) await openOrder(orderToken);
  else if (readDraft() || history.state?.cart) renderCart();
  else showScreen("menu");
  window.setInterval(() => void refreshAvailability(), MENU_REFRESH_MS);
}
void main();
