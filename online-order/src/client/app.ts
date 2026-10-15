// 客人掃碼點餐頁（docs/44 §4.2）。菜單文字一律 textContent，不拼 HTML。
import { drawExperience, experienceMini } from "./brew";
import { addLine, changeQty, checkCart, removeLine, type CartLine } from "./cart";
import {
  BREW_TAB, experienceView, greeting, itemBadge, itemSoldOut, money, presentationBadges, priceText, quizResult,
  menuTabs, minimumGate, minimumNotice, OTHER_TAB, PICKS_TAB, retailGroups, retailSoldOut, staffPicks, TAKEAWAY_TAB, tableCodeFromPath,
  upsellSuggestions, visibleItems, type ExperienceView, type MenuTab, type UpsellPick,
} from "./logic";
import { isRetailLine, type TableMinimum } from "../pricing";
import {
  UPSELL_ROLES, type MenuItemView, type MenuRetailView, type MenuSnapshot, type TableView, type UpsellRole,
} from "./types";

const POLL_MS = 5000;
const MENU_REFRESH_MS = 15000;
const CART_KEY = "lk_cart_v1";
const DRAFT_KEY = "lk_order_draft_v1";
const ORDER_PATH = /^\/order\/([A-Za-z0-9_-]{32,64})\/?$/;
const UPSELL_KEY = "lk_upsell_skip_v1";

interface StoreStatus { accepting: boolean; within_hours?: boolean; turnstile_site_key?: string | null; linepay?: boolean }
type PayMethod = "CASH" | "LINE_PAY";
interface InvoiceInput { carrier: string | null; tax_id: string | null }
const PAY_KEY = "lk_pay_v1";
const PARTY_KEY = "lk_party_v1";
const ADD_ON_KEY = "lk_add_on_v1";
const PARTY_MAX = 50;
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
  /** 重新付款要先過人機驗證（舊版雲端沒有這欄）。 */
  repay_challenge?: boolean;
  /** 店內作廢／退貨後累計退了多少（舊版雲端沒有這欄）。 */
  refunded_amount?: number;
}
interface OrderCreated { token: string; status: string; total: number }
interface Draft {
  idempotency_key: string; table_code: string | null; payment_method: PayMethod; invoice?: InvoiceInput;
  note: string; lines: CartLine[]; blocked?: boolean; party_size?: number | null; add_on?: boolean;
}
interface TurnstileApi {
  render: (container: HTMLElement, options: { sitekey: string; callback: (token: string) => void; "expired-callback": () => void; "error-callback": () => void }) => string;
  reset: (widgetId: string) => void;
}
declare global { interface Window { turnstile?: TurnstileApi; onTurnstileReady?: () => void } }

let menu: MenuSnapshot | null = null;
let tableCode: string | null = null;
/** 這桌的低消（2026-10-14 店主）；外帶或沒設＝null。 */
let tableMinimum: TableMinimum | null = null;
let cart: CartLine[] = [];
let status: StoreStatus | null = null;
let activeOrder: string | null = null;
/** 目前打開的分頁（null＝預設第一個，通常是店員推薦）。 */
let activeTab: string | null = null;
/** 問答區是否打開（「不知道點什麼？」按鈕）。 */
let quizOpen = false;
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
/** 正在前往 LINE Pay（驗證中或等付款連結）：付款鈕鎖住，輪詢重畫也不解鎖（Codex 審查）。 */
let payStarting = false;
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
    history.pushState({ menuTab: activeTab, cart: true }, "");
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
  const fallback = document.querySelector<HTMLElement>(`#list .item[data-item-id="${itemId}"] .item-detail`)
    ?? document.querySelector<HTMLElement>("#tabs button");
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
    if (group.min_select === 0 && group.max_select === 1) {
      const label = el("label", "opt-choice");
      const input = el("input"); input.type = "radio"; input.name = `group-${group.id}`;
      input.value = ""; input.checked = true;
      label.append(input, el("span", "", "不選擇")); field.append(label);
    }
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
      const optionIds = Array.from(groups.querySelectorAll<HTMLInputElement>('input:checked:not([value=""])'), (input) => Number(input.value));
      try {
        cart = addLine(menu, cart, { item_id: item.id, option_ids: optionIds, qty: Number(qty.value) });
        saveCart(); renderFooter(); closeDetail(); clearMessage();
        // 從購物車的加點開的：當場重畫購物車（新的一行、合計、低消），不必回菜單再進來。
        if (!$("cart-view").hidden) renderCart();
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
function selectTab(key: string, push = true): void {
  activeTab = key;
  if (push) history.pushState({ menuTab: key }, "");
  if (menu) renderMenu(menu);
  $("tabs").scrollIntoView({ block: "start" });
}
/** 分頁內容：店員推薦照店主排的順序、手沖體驗是卡片、帶著走依商品分類分組，其他就是那一類的品項。 */
function tabContent(snapshot: MenuSnapshot, tab: MenuTab): HTMLElement[] {
  if (tab.key === PICKS_TAB) {
    // 體驗卡放進和「手沖體驗」分頁一樣的兩欄卡片格（相鄰的放同一格），不然會被撐成整個螢幕寬。
    const nodes: HTMLElement[] = [];
    for (const pick of staffPicks(snapshot)) {
      if (pick.kind !== "experience") {
        nodes.push(pick.kind === "item" ? itemRow(pick.item, true) : retailRow(pick.product));
        continue;
      }
      const last = nodes[nodes.length - 1];
      const deck = last?.classList.contains("brew-deck") ? last : el("div", "brew-deck");
      if (deck !== last) nodes.push(deck);
      deck.append(experienceMini(pick.view, (from) => openExperience(pick.view, from)));
    }
    return nodes;
  }
  if (tab.key === BREW_TAB) {
    const deck = el("div", "brew-deck");
    deck.append(...(snapshot.experiences ?? [])
      .map((experience) => experienceView(snapshot, experience))
      .filter((view): view is ExperienceView => view !== null)
      .map((view) => experienceMini(view, (from) => openExperience(view, from))));
    return [deck];
  }
  if (tab.key === TAKEAWAY_TAB) {
    return retailGroups(snapshot).map((group) => {
      const box = el("div", "take-home-group");
      box.append(el("h3", "take-home-category", group.category), ...group.products.map(retailRow));
      return box;
    });
  }
  const known = new Set(snapshot.categories.map((category) => category.id));
  return visibleItems(snapshot.items)
    .filter((item) => tab.key === OTHER_TAB
      ? item.category_id === null || !known.has(item.category_id)
      : item.category_id === tab.categoryId)
    .map((item) => itemRow(item));
}
const TAB_NOTES: Record<string, string> = {
  [PICKS_TAB]: "店員最近愛的，慢慢選，慢慢喝。",
  [BREW_TAB]: "點一張卡，抽一支今天的豆子。",
  [TAKEAWAY_TAB]: "付款後請到櫃檯領取。",
};
/** 掃碼直接進完整菜單（店主 2026-10-10）：上方問候語＋「不知道點什麼？」，分類列預設打開店員推薦。 */
function renderMenu(snapshot: MenuSnapshot): void {
  $("hours-note").textContent = "目前非營業時間，可先看看菜單。";
  $("hours-note").hidden = status?.within_hours !== false;
  const key = JSON.stringify([snapshot, activeTab, quizOpen, new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei" }).format(new Date())]);
  if (key === renderedMenuKey) return;
  renderedMenuKey = key;
  const tabs = menuTabs(snapshot);
  const current = tabs.find((tab) => tab.key === activeTab) ?? tabs[0];
  $("tabs").replaceChildren(...tabs.map((tab) => {
    const on = tab.key === current?.key;
    const node = button(tab.label, () => selectTab(tab.key), on ? "tab tab-on" : "tab");
    node.setAttribute("aria-pressed", String(on)); return node;
  }));
  $("tabs").hidden = tabs.length === 0;
  const note = current ? TAB_NOTES[current.key] : undefined;
  $("tab-note").textContent = note ?? ""; $("tab-note").hidden = note === undefined;
  const list = $("list");
  list.replaceChildren(...(current ? tabContent(snapshot, current) : []));
  if (!list.childElementCount) list.append(el("p", "empty-state", "菜單準備中，請洽櫃台點餐。"));
  $("shortcuts").replaceChildren(...(snapshot.quiz ? [button("不知道點什麼？", () => startQuiz(), "shortcut quiz-entry")] : []));
  renderQuiz(snapshot);
}
function startQuiz(): void {
  quizAnswers = []; quizOpen = true;
  if (menu) renderQuiz(menu);
  $("quiz").scrollIntoView({ block: "start" });
}
function closeQuiz(): void {
  quizAnswers = null; quizOpen = false;
  if (menu) renderQuiz(menu);
}
function answerQuiz(answer: number | null): void {
  if (quizAnswers === null) return;
  quizAnswers = answer === null ? quizAnswers.slice(0, -1) : [...quizAnswers, answer];
  if (menu) renderQuiz(menu);
  // 換題／出結果後把問答區頂端帶回畫面（不被上方固定列擋住）。
  $("quiz").scrollIntoView({ block: "start" });
}
/** 「不知道點什麼」（docs/63 §2 M2a）：一題一題問，答完給 1 主推＋最多 2 備選；對不上就給完整菜單。 */
function renderQuiz(snapshot: MenuSnapshot): void {
  const quiz = snapshot.quiz;
  $("quiz").hidden = !quizOpen || quiz === undefined;
  const body = $("quiz-body");
  if (quiz === undefined) { body.replaceChildren(); return; }
  if (quizAnswers === null) { body.replaceChildren(); return; }
  const step = quizAnswers.length;
  const question = quiz.questions[step];
  if (question !== undefined) {
    const options = el("div", "quiz-options");
    options.append(...question.options.map((option, index) => button(option.label, () => answerQuiz(index), "quiz-option")));
    const nav = el("div", "quiz-nav");
    nav.append(button(step === 0 ? "先不用" : "上一題", () => {
      if (step === 0) closeQuiz(); else answerQuiz(null);
    }, "link-button"));
    body.replaceChildren(el("p", "quiz-step", `第 ${step + 1} / ${quiz.questions.length} 題`),
      el("h3", "quiz-prompt", question.prompt), options, nav);
    return;
  }
  const picks = quizResult(snapshot, quizAnswers);
  const again = button("重新回答", startQuiz, "link-button");
  if (picks.length === 0) {
    body.replaceChildren(el("p", "quiz-empty", "今天沒有剛好符合的，看看完整菜單吧。"),
      button("看完整菜單", () => { closeQuiz(); $("tabs").scrollIntoView({ block: "start" }); }, "action"), again);
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
function readParty(): number | null {
  const value = Number(sessionStorage.getItem(PARTY_KEY));
  return Number.isInteger(value) && value >= 1 && value <= PARTY_MAX ? value : null;
}
function readAddOn(): boolean { return sessionStorage.getItem(ADD_ON_KEY) === "1"; }
/** 「我是加點」：沒達低消才出現，勾了就不擋（店主 2026-10-15）；真假由店員現場判斷。 */
function addOnField(onChange: () => void): HTMLElement {
  const box = el("div", "add-on-field");
  const label = el("label", "add-on-choice");
  const input = el("input") as HTMLInputElement;
  input.id = "add-on"; input.type = "checkbox"; input.checked = readAddOn();
  const hint = el("p", "add-on-hint", "加點不用達低消，店員會在現場確認。");
  hint.hidden = !input.checked;
  input.addEventListener("change", () => {
    if (input.checked) sessionStorage.setItem(ADD_ON_KEY, "1"); else sessionStorage.removeItem(ADD_ON_KEY);
    hint.hidden = !input.checked;
    onChange();
  });
  label.append(input, el("span", "", "我是加點（同桌已經點過）"));
  box.append(label, hint);
  return box;
}
/** 低消說明＋（每人一份的桌）用餐人數；人數一改就重算能不能送出。 */
function minimumField(onChange: () => void): HTMLElement | null {
  const notice = minimumNotice(tableMinimum);
  if (notice === null) return null;
  const box = el("div", "minimum-box");
  box.append(el("p", "", notice));
  if (tableMinimum?.mode === "per_person_item") {
    const label = el("label", "party-field", "用餐人數");
    const input = el("input") as HTMLInputElement;
    input.id = "party-size"; input.type = "number"; input.inputMode = "numeric";
    input.min = "1"; input.max = String(PARTY_MAX); input.value = String(readParty() ?? "");
    input.addEventListener("input", () => {
      const value = Number(input.value);
      if (Number.isInteger(value) && value >= 1 && value <= PARTY_MAX) sessionStorage.setItem(PARTY_KEY, String(value));
      else sessionStorage.removeItem(PARTY_KEY);
      onChange();
    });
    label.append(input); box.append(label);
  }
  return box;
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
  const shortfall = el("p", "field-error minimum-shortfall");
  const gate = () => minimumGate(tableMinimum, priced.total, priced.lines, readParty(), readAddOn());
  // 低消緊接在合計下面：客人一眼看到還差多少，不必捲到付款方式下面找。
  let refreshMinimum = (): void => undefined;
  const minimum = minimumField(() => refreshMinimum());
  const addOn = addOnField(() => refreshMinimum());
  if (minimum) body.append(minimum, shortfall, addOn);
  const upsell = upsellBlock();
  if (upsell) body.append(upsell);
  const payment = paymentChoice();
  body.append(payment.node);
  body.append(el("p", "take-home-note", "現金訂單請在送出後 10 分鐘內結帳，逾時自動取消。"));
  if (cart.some(isRetailLine)) body.append(el("p", "take-home-note", "帶著走的商品請到櫃檯領取。"));
  const submit = button(payment.method() === "LINE_PAY" ? "用 LINE Pay 付款" : "送出現金訂單", () => {
    const reason = gate().block;
    if (reason !== null) { showMessage(reason); return; }
    const invoice = payment.invoice();
    if (invoice === null) { showMessage("手機條碼是 / 開頭共 8 碼；統一編號是 8 位數字。"); return; }
    void startCheckout(payment.method(), invoice);
  });
  payment.onChange(() => { submit.textContent = payment.method() === "LINE_PAY" ? "用 LINE Pay 付款" : "送出現金訂單"; });
  refreshMinimum = (): void => {
    const { block, offerAddOn } = gate();
    shortfall.textContent = block ?? ""; shortfall.hidden = block === null; submit.disabled = block !== null;
    addOn.hidden = !offerAddOn;
  };
  refreshMinimum();
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
  const box = $("challenge-widget"); box.hidden = false;
  if (widgetId !== null) api.reset(widgetId);
  else widgetId = api.render($("challenge-widget"), {
    sitekey, callback: (token) => { challengeToken = token; },
    "expired-callback": () => { challengeToken = ""; },
    "error-callback": () => { challengeToken = ""; },
  });
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const timer = window.setInterval(() => {
      if (challengeToken) { clearInterval(timer); box.hidden = true; resolve(challengeToken); }
      else if (Date.now() - started > 120000) { clearInterval(timer); box.hidden = true; reject(new Error("challenge_unavailable")); }
    }, 200);
  });
}
function newDraft(): Draft {
  return { idempotency_key: crypto.randomUUID(), table_code: tableCode, payment_method: "CASH",
    note: "", lines: cart.map((line) => isRetailLine(line) ? { ...line } : { ...line, option_ids: [...line.option_ids] }),
    party_size: tableMinimum?.mode === "per_person_item" ? readParty() : null };
}
async function startCheckout(method: PayMethod = "CASH", invoice: InvoiceInput = { carrier: null, tax_id: null }): Promise<void> {
  if (menu === null || checkoutStarting || submitting) return;
  checkoutStarting = true;
  try {
  const priced = checkCart(menu, cart);
  if (!priced.ok) { showMessage(cartError(new Error(priced.reason))); return; }
  const live = await getJson<StoreStatus>("/api/status"); status = live.body;
  if (!status?.accepting) { showMessage(status?.within_hours === false ? "目前非營業時間，可先看看菜單。" : "目前暫停接單，請到櫃台點餐。"); return; }
  if (!status.turnstile_site_key) { showMessage("驗證服務尚未設定，請到櫃台點餐。"); return; }
  if (method === "LINE_PAY" && !status.linepay) { showMessage("LINE Pay 暫時不能用，請改用現金。"); return; }
  // 加點以送出當下的購物車重算：已達低消就不帶，POS 才不會把一般單標成加點。
  const addOn = minimumGate(tableMinimum, priced.total, priced.lines, readParty(), readAddOn()).addOn;
  const draft = { ...newDraft(), payment_method: method, invoice, ...(addOn ? { add_on: true } : {}) };
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
        ...(draft.party_size ? { party_size: draft.party_size } : {}),
        ...(draft.add_on ? { add_on: true } : {}),
        ...(draft.payment_method === "LINE_PAY" && draft.invoice ? { invoice: draft.invoice } : {}) }) });
    if (resp.ok) {
      const created = await resp.json() as OrderCreated;
      localStorage.removeItem(DRAFT_KEY); localStorage.removeItem(CART_KEY); sessionStorage.removeItem("lk_note");
      sessionStorage.removeItem(ADD_ON_KEY); // 下一張單由客人重新選，不沿用這次的加點
      cart = []; renderFooter(); history.pushState(null, "", `/order/${created.token}`);
      // LINE Pay：不用等保留的單直接去付款；要等 POS 確認限量的，留在訂單頁等「前往付款」。
      if (draft.payment_method === "LINE_PAY" && created.status !== "HOLD_REQUESTED" && await goLinePay(created.token)) return;
      await openOrder(created.token); return;
    }
    const result = await resp.json() as { error?: string; missing_amount?: number; missing_items?: number };
    if (resp.status === 409) {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ ...draft, blocked: true }));
      renderCart(); showMessage("訂單識別碼有衝突。請到櫃台確認，暫時不要重新送單。");
    } else if (resp.status === 422 || resp.status === 403 || resp.status === 404) {
      localStorage.removeItem(DRAFT_KEY);
      renderCart();
      showMessage(result.error === "minimum_not_met" ? minimumRejected(result) :
        result.error === "party_size_required" ? "這桌需要填寫用餐人數，請重新整理頁面後再送出。" :
        result.error === "sold_out" ? "品項已售完，請更新菜單後再選。" :
        result.error === "challenge_failed" ? "驗證未通過，請重新送出。" : "訂單內容有變，請重新確認後送出。");
    } else {
      renderCart(); showMessage("尚未確認訂單是否成立。請按「重試確認訂單」，不要重新選品項送單。");
    }
  } catch {
    renderCart(); showMessage("連線中斷，尚未確認訂單是否成立。請按「重試確認訂單」。");
  } finally { submitting = false; challengeToken = ""; }
}
/** 雲端以最新的低消規則擋下（店家剛改規則時，畫面上的規則可能較舊）。 */
function minimumRejected(result: { missing_amount?: number; missing_items?: number }): string {
  if (typeof result.missing_amount === "number") return `未達本桌低消，還差 ${money(result.missing_amount)}。`;
  if (typeof result.missing_items === "number") return `每位客人至少一份餐點，還差 ${result.missing_items} 份。`;
  return "未達本桌低消，請再加點後送出。";
}
/** 向雲端要 LINE Pay 付款連結並跳過去；失敗回 false（留在訂單頁顯示原因）。 */
function startPayment(token: string, challengeToken?: string): Promise<Response> {
  return fetch(`/api/orders/${token}/linepay`, {
    method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: challengeToken === undefined ? undefined : JSON.stringify({ turnstile_token: challengeToken }),
  });
}
function syncPayButton(): void {
  const pay = document.querySelector<HTMLButtonElement>("#order-body .pay-linepay");
  if (pay) pay.disabled = payStarting;
}
async function goLinePay(token: string, needsChallenge = false): Promise<boolean> {
  // 等驗證時連按、或輪詢重畫出新按鈕再按，都只走一次：每次送出都佔全店共用的付款名額。
  if (payStarting) return false;
  payStarting = true; syncPayButton();
  let leaving = false;
  try {
    // 重新付款要重新驗證（建單時那次已經用掉）：訂單頁說要驗證就先驗證、只送一次，
    // 不先空送一次去試——每次送出都算同網路每分鐘的付款名額（Codex 審查）。
    if (needsChallenge) showMessage("請完成安全驗證，再前往 LINE Pay。");
    let resp = await startPayment(token, needsChallenge ? await challenge() : undefined);
    if (needsChallenge) clearMessage();
    // 畫面上的狀態較舊（例如另一個分頁剛取消過）：雲端仍要求驗證，就地驗證後再送一次。
    if (resp.status === 403 && (await resp.clone().json() as { error?: string }).error === "challenge_required") {
      showMessage("請完成安全驗證，再前往 LINE Pay。");
      resp = await startPayment(token, await challenge());
      clearMessage();
    }
    if (!resp.ok) {
      const result = await resp.json() as { error?: string };
      showMessage(result.error === "hold_pending" ? "正在確認限量品項，確認後就能付款。"
        : result.error === "hold_expired" ? "保留時間已過，請回菜單重新點。"
        : result.error === "challenge_failed" ? "安全驗證沒有通過，請再按一次付款。"
        : "暫時連不上 LINE Pay，請稍後再試或到櫃檯付現金。");
      return false;
    }
    const { payment_url } = await resp.json() as { payment_url: string };
    location.assign(payment_url);
    leaving = true;
    return true;
  } catch (reason) {
    showMessage(reason instanceof Error && reason.message === "challenge_unavailable"
      ? "安全驗證暫時無法使用，請稍後再試或到櫃檯付現金。" : "連線中斷，請稍後再試。");
    return false;
  } finally {
    // 已經在跳往 LINE Pay 就維持鎖住，跳轉前的輪詢重畫不會再開放按鈕。
    if (!leaving) { payStarting = false; syncPayButton(); }
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
  if (view.fulfillment === "AWAITING") return "已付款。帶著走的商品請到櫃檯領取。";
  const linePay = linePayState(view);
  if (linePay !== null) return linePay;
  switch (view.status) {
    case "HOLD_REQUESTED": return "訂單已收到，正在確認限量品項。請稍候。";
    case "HELD": return "訂單已確認。請到櫃台付現金，付款後才會製作。";
    case "UNPAID": return "訂單已收到。請在送出後 10 分鐘內到櫃台結帳，逾時自動取消；付款後才會製作。";
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
    body.append(el("p", "order-line", `${line.name} × ${line.qty}　${money(line.line_total)}${line.take_home ? "　（帶著走）" : ""}`));
  }
  if (view.lines.some((line) => line.take_home) && view.fulfillment !== "HANDED_OVER" && view.status !== "REFUNDED") {
    body.append(el("p", "take-home-note", "帶著走的商品請到櫃檯領取。"));
  }
  body.append(el("p", "cart-total", `合計 ${money(view.total)}`));
  if (view.note) body.append(el("p", "", `備註：${view.note}`));
  const canPay = view.payment_method === "LINE_PAY" && (view.status === "UNPAID" || view.status === "PENDING") &&
    view.linepay_result !== "EXPIRED";
  if (canPay && activeOrder !== null) {
    const token = activeOrder;
    // 付款中（PENDING）沿用原連結、不用驗證；之前付過又取消／失敗的才要先驗證。
    const pay = button(view.status === "PENDING" ? "前往 LINE Pay 付款" : "用 LINE Pay 付款",
      () => void goLinePay(token, view.status !== "PENDING" && view.repay_challenge === true), "action pay-linepay");
    pay.disabled = payStarting;
    body.append(pay);
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
      if (input.value === "") continue; // Optional-group clear choice has no stock record.
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
  // 從 LINE Pay 按上一頁回來，瀏覽器可能直接還原跳轉前的頁面：付款鈕要解鎖、狀態重新查。
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    payStarting = false; syncPayButton();
    if (activeOrder !== null) void refreshOrder();
  });
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
  // 付款完成後就不再輪詢；客人切回這個分頁時重抓一次，店裡後來退了款也看得到（docs/44 §4.5 C6）。
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && activeOrder !== null) void refreshOrder();
  });
  window.addEventListener("popstate", (event: PopStateEvent) => {
    if (ORDER_PATH.test(location.pathname) || activeOrder) { location.reload(); return; }
    restoringHistory = true;
    closeDetail();
    activeTab = event.state?.menuTab ?? null;
    if (menu) renderMenu(menu);
    if (event.state?.cart) renderCart(); else showScreen("menu");
    restoringHistory = false;
  });
  if (!ORDER_PATH.test(location.pathname)) {
    activeTab = history.state?.menuTab ?? null;
    history.replaceState({ menuTab: activeTab, cart: history.state?.cart === true }, "");
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
      tableMinimum = tableResult.body.service_mode === "DINE_IN" ? tableResult.body.minimum ?? null : null;
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
