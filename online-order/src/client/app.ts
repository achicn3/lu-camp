// 客人掃碼點餐頁（docs/44 §4.2；O3c 先做「只能看」的電子菜單，送單在 O4）。
// 視覺：B1 夜墨金＋辰宇落雁體（店主 2026-10-02 定案）。所有菜單文字一律 textContent，不拼 HTML。
import { greeting, itemBadge, money, priceText, tableCodeFromPath } from "./logic";
import type { MenuItemView, MenuSnapshot, OptionGroupView, TableView } from "./types";

const SPLASH_MS = 1200;
const ALL = -1;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
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

async function getJson<T>(url: string): Promise<{ status: number; body: T | null }> {
  try {
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    return { status: resp.status, body: resp.ok ? ((await resp.json()) as T) : null };
  } catch {
    return { status: 0, body: null };
  }
}

/** 手寫字型子集：發佈時抽好的字，載入失敗就維持系統字型（頁面照常可用）。 */
async function loadHandFont(hash: string | null): Promise<void> {
  if (hash === null || !/^[0-9a-f]{64}$/.test(hash)) return;
  try {
    const face = new FontFace("LukengHand", `url(/fonts/${hash}.woff2)`);
    document.fonts.add(await face.load());
    document.documentElement.classList.add("hand-font-ready");
  } catch {
    // 字型只是加分，失敗不影響點餐
  }
}

function hideSplash(): void {
  document.body.classList.add("entered");
}

function startSplash(): void {
  const splash = $("splash");
  splash.addEventListener("click", hideSplash);
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.setTimeout(hideSplash, reduce ? 300 : SPLASH_MS);
}

function showMessage(text: string): void {
  const box = $("message");
  box.textContent = text;
  box.hidden = false;
}

function optionLine(group: OptionGroupView): HTMLElement {
  const row = el("div", "opt-group");
  const rule =
    group.min_select === 1 && group.max_select === 1
      ? "必選 1 項"
      : group.min_select > 0
        ? `至少 ${group.min_select} 項，最多 ${group.max_select} 項`
        : `可不選，最多 ${group.max_select} 項`;
  const head = el("div", "opt-group-head");
  head.append(el("span", "opt-group-name", group.name), el("span", "opt-group-rule", rule));
  row.append(head);
  const list = el("ul", "opt-list");
  for (const o of group.options) {
    const li = el("li", o.available && o.remaining !== 0 ? "opt" : "opt opt-off");
    li.append(el("span", "opt-name", o.name));
    const extra = !o.available || o.remaining === 0 ? "售完" : o.price_delta > 0 ? `+${money(o.price_delta)}` : "";
    if (extra) li.append(el("span", "opt-extra", extra));
    list.append(li);
  }
  row.append(list);
  return row;
}

function openDetail(item: MenuItemView): void {
  const sheet = $("sheet");
  const body = $("sheet-body");
  body.replaceChildren();
  if (item.photo) {
    const img = el("img", "sheet-photo");
    img.src = `/photos/${item.photo}.webp`;
    img.alt = item.name;
    body.append(img);
  }
  const title = el("h2", "sheet-title", item.name);
  title.id = "sheet-title";
  body.append(title);
  if (item.description) body.append(el("p", "sheet-desc", item.description));
  const price = priceNode(item);
  price.classList.add("sheet-price");
  body.append(price);
  for (const group of item.option_groups) body.append(optionLine(group));
  body.append(el("p", "sheet-note", "線上點餐即將開放，請先至櫃台點餐。"));
  sheet.hidden = false;
  document.body.classList.add("sheet-open");
  $("sheet-close").focus();
}

function closeDetail(): void {
  $("sheet").hidden = true;
  document.body.classList.remove("sheet-open");
}

/** 價格用 Cormorant 斜體數字；「起」是中文，另外用手寫字（Cormorant 沒有中文字形）。 */
function priceNode(item: MenuItemView): HTMLElement {
  const node = el("span", "item-price", money(item.unit_price));
  if (item.option_groups.length > 0) node.append(el("span", "price-from", " 起"));
  node.setAttribute("aria-label", priceText(item));
  return node;
}

function itemRow(item: MenuItemView): HTMLElement {
  const soldOut = item.remaining === 0;
  const button = el("button", soldOut ? "item item-soldout" : "item");
  button.type = "button";
  const photo = el("span", "item-photo");
  if (item.photo) {
    const img = el("img");
    img.src = `/photos/${item.photo}.webp`;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    photo.append(img);
  }
  const text = el("span", "item-text");
  text.append(el("span", "item-name", item.name));
  if (item.description) text.append(el("span", "item-desc", item.description));
  text.append(priceNode(item));
  const badge = itemBadge(item);
  button.append(photo, text);
  if (badge) button.append(el("span", soldOut ? "item-badge item-badge-off" : "item-badge", badge));
  button.addEventListener("click", () => openDetail(item));
  return button;
}

function renderMenu(menu: MenuSnapshot): void {
  const tabs = $("tabs");
  const list = $("list");
  let current = ALL;
  const categories = [{ id: ALL, name: "全部" }, ...menu.categories];
  const draw = () => {
    tabs.replaceChildren(
      ...categories.map((c) => {
        const tab = el("button", c.id === current ? "tab tab-on" : "tab", c.name);
        tab.type = "button";
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", String(c.id === current));
        tab.addEventListener("click", () => {
          current = c.id;
          draw();
        });
        return tab;
      }),
    );
    const shown = menu.items.filter((i) => current === ALL || i.category_id === current);
    list.replaceChildren(...shown.map(itemRow));
  };
  tabs.hidden = menu.categories.length < 2;
  draw();
}

async function main(): Promise<void> {
  startSplash();
  $("greeting").textContent = greeting(new Date());
  $("sheet-close").addEventListener("click", closeDetail);
  $("sheet").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeDetail();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDetail();
  });

  const code = tableCodeFromPath(location.pathname);
  const [menu, table] = await Promise.all([
    getJson<MenuSnapshot>("/api/menu"),
    code ? getJson<TableView>(`/api/tables/${code}`) : Promise.resolve(null),
  ]);
  if (table !== null) {
    if (table.body !== null) {
      $("table").textContent = table.body.service_mode === "TAKEOUT" ? "外帶" : `桌 ${table.body.label}`;
      $("table").hidden = false;
    } else if (table.status === 404) {
      showMessage("這個 QR 已經失效了，請洽櫃台。");
    }
  }
  if (menu.body === null) {
    showMessage(menu.status === 404 ? "菜單準備中，請洽櫃台點餐。" : "連不上網路，請稍後再試或至櫃台點餐。");
    return;
  }
  void loadHandFont(menu.body.font);
  renderMenu(menu.body);
  $("footer").hidden = false;
}

void main();
