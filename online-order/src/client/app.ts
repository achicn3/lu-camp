// 客人掃碼點餐頁（docs/44 §4.2）。菜單文字一律 textContent，不拼 HTML。
import { addLine, changeQty, checkCart, removeLine, type CartLine } from "./cart";
import { greeting, itemBadge, itemSoldOut, money, presentationBadges, priceText, tableCodeFromPath, visibleItems } from "./logic";
import type { MenuItemView, MenuSnapshot, TableView } from "./types";

const SPLASH_MS = 1200;
const POLL_MS = 5000;
const MENU_REFRESH_MS = 15000;
const ALL = -1;
const CART_KEY = "lk_cart_v1";
const DRAFT_KEY = "lk_order_draft_v1";
const ORDER_PATH = /^\/order\/([A-Za-z0-9_-]{32,64})\/?$/;

interface StoreStatus { accepting: boolean; turnstile_site_key?: string | null }
interface OrderLine { name: string; qty: number; line_total: number }
interface OrderView {
  status: string; table_label: string | null; service_mode: string;
  total: number; note: string | null; created_at: string; lines: OrderLine[];
}
interface OrderCreated { token: string; status: string; total: number }
interface Draft {
  idempotency_key: string; table_code: string | null; payment_method: "CASH";
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
let activeDetail: number | null = null;
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
    return parsed.filter((line): line is CartLine => typeof line === "object" && line !== null &&
      Number.isInteger(line.item_id) && Number.isInteger(line.qty) && Array.isArray(line.option_ids) &&
      line.option_ids.every(Number.isInteger));
  } catch { return []; }
}
function readDraft(): Draft | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? "null");
    if (typeof value !== "object" || value === null) return null;
    const draft = value as Partial<Draft>;
    return typeof draft.idempotency_key === "string" && /^[A-Za-z0-9_-]{16,64}$/.test(draft.idempotency_key) &&
      (draft.table_code === null || typeof draft.table_code === "string") && draft.payment_method === "CASH" &&
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
function startSplash(): void {
  $("splash").addEventListener("click", () => document.body.classList.add("entered"));
  window.setTimeout(() => document.body.classList.add("entered"),
    matchMedia("(prefers-reduced-motion: reduce)").matches ? 300 : SPLASH_MS);
}
function showScreen(screen: "menu" | "cart" | "order"): void {
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
  activeDetail = null;
  $("sheet").hidden = true;
  document.body.classList.remove("sheet-open");
}
function openDetail(item: MenuItemView): void {
  if (menu === null) return;
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
      if (input.disabled) label.append(el("span", "opt-off", "售完"));
      field.append(label);
    }
    groups.append(field);
  }
  body.append(groups);
  if (itemSoldOut(item)) {
    body.append(el("p", "sheet-note", "今日售完，請選其他品項。"));
  } else {
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
    controls.append(qty, add);
    body.append(controls, error);
  }
  $("sheet").hidden = false; document.body.classList.add("sheet-open"); $("sheet-close").focus();
}
function cartError(reason: unknown): string {
  const code = reason instanceof Error ? reason.message : "";
  if (code === "invalid_options") return "請依每組規則選好選項。";
  if (code === "sold_out") return "品項或選項份數不足，請調整數量。";
  if (code === "invalid_qty" || code === "too_many") return "每項最多 10 份、整張單最多 50 份及 20 項。";
  return "購物車已變動，請重新確認菜單。";
}
function itemRow(item: MenuItemView): HTMLElement {
  const soldOut = itemSoldOut(item);
  const row = button("", () => openDetail(item), soldOut ? "item item-soldout" : "item");
  const photo = el("span", "item-photo");
  if (item.photo) {
    const img = el("img"); img.src = `/photos/${item.photo}.webp`; img.alt = "";
    img.loading = "lazy"; img.decoding = "async"; photo.append(img);
  }
  const copy = el("span", "item-text"); copy.append(el("span", "item-name", item.name));
  if (item.presentation?.flavor_description) copy.append(el("span", "item-flavor", item.presentation.flavor_description));
  if (item.presentation?.audience_description) copy.append(el("span", "item-audience", item.presentation.audience_description));
  if (item.description && !item.presentation?.flavor_description) copy.append(el("span", "item-desc", item.description));
  const labels = presentationBadges(item, new Date());
  if (labels.length) copy.append(el("span", "item-labels", labels.join(" · ")));
  copy.append(priceNode(item)); row.append(photo, copy);
  const badge = soldOut ? "今日售完" : itemBadge(item);
  if (badge) row.append(el("span", soldOut ? "item-badge item-badge-off" : "item-badge", badge));
  return row;
}
function renderMenu(snapshot: MenuSnapshot): void {
  const tabs = $("tabs"); const list = $("list");
  if (!snapshot.categories.some((category) => category.id === activeCategory)) activeCategory = ALL;
  const categories = [{ id: ALL, name: "全部" }, ...snapshot.categories];
  const draw = () => {
    tabs.replaceChildren(...categories.map((category) => {
      const tab = button(category.name, () => { activeCategory = category.id; draw(); }, category.id === activeCategory ? "tab tab-on" : "tab");
      tab.setAttribute("role", "tab"); tab.setAttribute("aria-selected", String(category.id === activeCategory)); return tab;
    }));
    list.replaceChildren(...visibleItems(snapshot.items).filter((item) => activeCategory === ALL || item.category_id === activeCategory).map(itemRow));
  };
  tabs.hidden = snapshot.categories.length < 2; draw();
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
    body.append(button("清空購物車", () => { cart = []; saveCart(); renderFooter(); renderCart(); }, "quiet-action"));
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
  const noteLabel = el("label", "note-label", "備註（最多 60 字）");
  const note = el("textarea") as HTMLTextAreaElement; note.id = "order-note"; note.maxLength = 60;
  note.rows = 2; note.value = sessionStorage.getItem("lk_note") ?? "";
  note.addEventListener("input", () => sessionStorage.setItem("lk_note", note.value));
  noteLabel.append(note); body.append(noteLabel);
  body.append(el("p", "payment-note", "付款方式：現金 · 到櫃台付款後才會製作。"));
  body.append(button("送出現金訂單", () => void startCheckout(note.value)));
  showScreen("cart");
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
    note: note.trim(), lines: cart.map((line) => ({ ...line, option_ids: [...line.option_ids] })) };
}
async function startCheckout(note: string): Promise<void> {
  if (menu === null || checkoutStarting || submitting) return;
  checkoutStarting = true;
  try {
  const priced = checkCart(menu, cart);
  if (!priced.ok) { showMessage(cartError(new Error(priced.reason))); return; }
  if ([...note].length > 60) { showMessage("備註最多 60 字。"); return; }
  const live = await getJson<StoreStatus>("/api/status"); status = live.body;
  if (!status?.accepting) { showMessage("目前暫停接單，請到櫃台點餐。"); return; }
  if (!status.turnstile_site_key) { showMessage("驗證服務尚未設定，請到櫃台點餐。"); return; }
  const draft = newDraft(note);
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
        payment_method: draft.payment_method, note: draft.note, lines: draft.lines, turnstile_token: token }) });
    if (resp.ok) {
      const created = await resp.json() as OrderCreated;
      localStorage.removeItem(DRAFT_KEY); localStorage.removeItem(CART_KEY); sessionStorage.removeItem("lk_note");
      cart = []; renderFooter(); history.pushState(null, "", `/order/${created.token}`); await openOrder(created.token); return;
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
function orderState(view: OrderView): string {
  switch (view.status) {
    case "HOLD_REQUESTED": return "訂單已收到，正在確認限量品項。請稍候。";
    case "HELD": return "訂單已確認。請到櫃台付現金，付款後才會製作。";
    case "UNPAID": return "訂單已收到。請到櫃台付現金，付款後才會製作。";
    case "PAID": return "已付款，謝謝你。";
    case "REJECTED": return "份數不足，這張訂單未成立。請回菜單重新選。";
    case "CANCELLED": return "這張訂單已取消。";
    case "REFUNDED": return "這張訂單已退款。";
    case "PARTIALLY_REFUNDED": return "這張訂單已部分退款。";
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
  for (const line of view.lines) body.append(el("p", "order-line", `${line.name} × ${line.qty}　${money(line.line_total)}`));
  body.append(el("p", "cart-total", `合計 ${money(view.total)}`));
  if (view.note) body.append(el("p", "", `備註：${view.note}`));
  body.append(el("p", "order-hint", "可儲存此頁網址，稍後查看付款狀態。"));
  if (view.status === "REJECTED" || view.status === "CANCELLED")
    body.append(button("返回菜單", () => { history.pushState(null, "", tableCode ? `/t/${tableCode}` : "/"); activeOrder = null; showScreen("menu"); }));
  if (["PAID", "REJECTED", "CANCELLED", "REFUNDED"].includes(view.status) && pollTimer !== null) {
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
  if (pollTimer !== null) clearInterval(pollTimer);
  pollTimer = window.setInterval(() => void refreshOrder(), POLL_MS);
  await refreshOrder();
}
async function main(): Promise<void> {
  startSplash(); $("greeting").textContent = greeting(new Date());
  $("sheet-close").addEventListener("click", closeDetail);
  $("sheet").addEventListener("click", (event) => { if (event.target === event.currentTarget) closeDetail(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDetail(); });
  $("back-menu").addEventListener("click", () => showScreen("menu"));
  window.addEventListener("popstate", () => location.reload());
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
  else if (readDraft()) renderCart();
  else showScreen("menu");
  window.setInterval(() => void refreshAvailability(), MENU_REFRESH_MS);
}
void main();
