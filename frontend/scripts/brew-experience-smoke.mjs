// 手沖體驗卡＋加購煙霧（docs/63 §4、§6；M1c）：真 backend／Postgres ＋ 本機 Worker/D1 ＋ 真客人頁。
// 後台建體驗卡 → 發佈 → 客人首頁看到小卡 → 六種抽卡動畫逐一跑完翻開 → 補選溫度 → 加入購物車
// → 購物車品名帶體驗名稱、價格照原品項＋選項 → 咖啡配甜點的加購出現、略過後不再出現 → 送出現金單
// → POS 收到的線上單品名可辨識是體驗、品項仍是原品項。
// 只准對隔離測試環境執行（SMOKE_ALLOW_WRITE=1）。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium, devices } from "playwright";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "需明確允許寫入隔離測試環境");
const API = process.env.SMOKE_API ?? "http://localhost:8114";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8799";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp/lu-camp-shots/brew-experience");
mkdirSync(SHOTS, { recursive: true });

let token = "";
async function api(method, path, body) {
  const response = await fetch(`${API}/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  assert.ok(response.ok, `${method} ${path}: ${response.status} ${text}`);
  return text ? JSON.parse(text) : null;
}
async function waitFor(fn, label, ms = 30000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    const result = await fn();
    if (result) return result;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Timed out: ${label}`);
}
const results = [];
const ok = (name, pass, detail = "") => {
  results.push(pass);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
};

token = (await api("POST", "/auth/login", {
  username: process.env.SMOKE_USERNAME ?? "dev-manager",
  password: process.env.SMOKE_PASSWORD ?? "dev-test-123456",
})).access_token;
const run = randomUUID().slice(0, 6);
const brew = await api("POST", "/menu-items", { name: `手沖咖啡-${run}`, unit_price: "220", category: `手沖-${run}` });
const beans = await api("POST", "/menu-option-groups", {
  name: `豆子-${run}`, min_select: 1, max_select: 1,
  options: [{ name: "蜜桃蹦蹦", price_delta: "60" }, { name: "天堂鳥莊園", price_delta: "20" }],
});
const temp = await api("POST", "/menu-option-groups", {
  name: `溫度-${run}`, min_select: 1, max_select: 1, options: [{ name: "熱" }, { name: "冰", price_delta: "10" }],
});
await api("PUT", `/menu-items/${brew.id}/option-groups`, { group_ids: [beans.id, temp.id] });
const latte = await api("POST", "/menu-items", { name: `拿鐵-${run}`, unit_price: "150", category: `咖啡-${run}` });
const cake = await api("POST", "/menu-items", { name: `戚風-${run}`, unit_price: "90", category: `甜點-${run}` });
const role = (role) => ({ flavor_description: null, audience_description: null, is_recommended: false, is_new: false,
  limited_on: null, show_remaining: true, low_stock_threshold: 5, hide_sold_out: false, role });
await api("PUT", `/online-order/menu-items/${latte.id}/presentation`, role("coffee"));
await api("PUT", `/online-order/menu-items/${cake.id}/presentation`, role("dessert"));
const title = `蜜桃蹦蹦體驗-${run}`;
const card = await api("POST", "/online-order/experiences", {
  menu_item_id: brew.id, option_ids: [beans.options[0].id], title, tag: "清甜果香", origin: "柯契爾｜水洗",
  notes: "水蜜桃・白桃・荔枝", description: "以飽滿的水蜜桃與白桃甜香為主。",
  includes: [{ title: "咖啡豆", detail: "這支豆子現磨、單杯份量" }, { title: "現場體驗", detail: "約 20 分鐘" }],
  theme: "peach", art: "peach", effect: "soar", sort_order: 0,
});
const settings = await api("GET", "/settings");
await api("PATCH", "/settings", { dine_in_tables: [...new Set([...(settings.dine_in_tables ?? []), "A1"])] });
await api("POST", "/online-order/publish");
const code = (await api("GET", "/online-order/status")).tables.find((t) => t.label === "A1")?.code;
assert.ok(code);
await api("PUT", "/online-orders/accepting", { accepting: true });

const browser = await chromium.launch();
const page = await browser.newPage({ ...devices["iPhone 13"] });
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
const mini = () => page.locator(`.brew-mini[data-experience-id="${card.id}"]`);
try {
  await page.goto(`${ORDER}/t/${code}`, { waitUntil: "networkidle" });
  await mini().waitFor();
  ok("首頁出現手沖體驗小卡", (await mini().innerText()).includes(title));
  ok("首頁入口有「手沖體驗」", (await page.getByRole("button", { name: "手沖體驗", exact: true }).count()) === 1);
  const artLoaded = await mini().locator("img.brew-art").evaluate((img) =>
    img.decode().then(() => img.naturalWidth > 0, () => false));
  ok("小卡插畫（水彩 JPG）真的載入", artLoaded);
  await page.locator("#experiences").screenshot({ path: join(SHOTS, "01-deck.png") });

  // 六種抽卡動畫逐一跑：每種都要翻到正面、出現「看體驗內容」，期間不得有 JS 例外。
  for (const effect of ["soar", "truck", "smash", "seal", "shuffle", "bloom"]) {
    await api("PUT", `/online-order/experiences/${card.id}`, {
      ...card, id: undefined, effect,
    });
    await api("POST", "/online-order/publish");
    await page.goto(`${ORDER}/t/${code}`, { waitUntil: "networkidle" });
    await mini().click();
    await page.locator(".brew-peek.brew-on").waitFor({ timeout: 20000 });
    const shine = await page.locator(".brew-front.brew-shine").count();
    ok(`動畫「${effect}」跑完並翻開`, shine === 1);
    // 卡片本身動過 opacity 會被瀏覽器壓平成 2D，翻面後背面（鏡像）透出來——咖啡車曾經這樣。
    const flattened = await page.locator(".brew-stage > .brew-card").first().evaluate((card) =>
      card.getAnimations().some((a) => a.effect.getKeyframes().some((k) => "opacity" in k)) ||
      getComputedStyle(card).opacity !== "1");
    ok(`動畫「${effect}」沒有動到卡片透明度（翻開看到的是正面）`, !flattened);
    await page.screenshot({ path: join(SHOTS, `02-${effect}.png`) });
    await page.getByRole("button", { name: "關閉" }).click();
    await page.locator(".brew-stage").waitFor({ state: "detached" });
  }

  // 翻開後看體驗內容、補選溫度、加入購物車
  await mini().click();
  await page.locator(".brew-peek.brew-on").waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "看體驗內容" }).click();
  const panel = page.getByRole("region", { name: "體驗內容" });
  await panel.waitFor();
  ok("體驗內容列出包含項目", (await panel.innerText()).includes("這支豆子現磨、單杯份量"));
  ok("價格＝原品項＋預選豆子（還要選溫度，標「起」）", (await panel.locator(".brew-price").innerText()).includes("$280 起"));
  await panel.getByRole("button", { name: "加入購物車" }).click();
  ok("沒選溫度不能加入", (await panel.locator(".field-error").innerText()).includes("請先選好"));
  await panel.getByText("冰", { exact: true }).click();
  ok("選了冰（+10）價格更新", (await panel.locator(".brew-price").innerText()).trim() === "$290");
  await page.screenshot({ path: join(SHOTS, "03-panel.png") });
  await panel.getByRole("button", { name: "加入購物車" }).click();
  await page.locator(".brew-stage").waitFor({ state: "detached" });
  await page.getByRole("button", { name: /購物車 1 份/ }).click();
  const cartText = await page.locator("#cart-body").innerText();
  ok("購物車品名帶體驗名稱與原品項選項", cartText.includes(`${title}・手沖咖啡-${run}（蜜桃蹦蹦、冰）`), cartText.replace(/\n/g, " "));
  ok("購物車金額照原品項計價", cartText.includes("合計 $290"));
  ok("體驗沒有咖啡豆／濾掛可推時不顯示加購", (await page.locator(".upsell").count()) === 0);

  // 咖啡配甜點：加一杯拿鐵，購物車下方出現「配個甜的？」；略過後不再出現
  await page.getByRole("button", { name: "← 返回菜單" }).click();
  await page.getByRole("button", { name: `咖啡-${run}`, exact: true }).click().catch(async () => {
    await page.getByRole("button", { name: "全部", exact: true }).click();
  });
  await page.locator(".item", { hasText: `拿鐵-${run}` }).getByRole("button", { name: /加入/ }).click();
  await page.getByRole("button", { name: /購物車 2 份/ }).click();
  const upsell = page.getByRole("region", { name: "加購推薦" });
  await upsell.waitFor();
  const upsellText = await upsell.innerText();
  ok("咖啡配甜點：推戚風", upsellText.includes("配個甜的？") && upsellText.includes(`戚風-${run}`), upsellText.replace(/\n/g, " "));
  await page.screenshot({ path: join(SHOTS, "04-upsell.png"), fullPage: true });
  await upsell.getByRole("button", { name: "不用了" }).click();
  ok("略過後消失", (await page.locator(".upsell").count()) === 0);
  await page.getByRole("button", { name: "← 返回菜單" }).click();
  await page.getByRole("button", { name: /購物車 2 份/ }).click();
  ok("略過過的不再推", (await page.locator(".upsell").count()) === 0);

  // 送出現金單 → POS 收到：品名可辨識體驗、品項仍是原品項
  const note = `體驗-${run}`;
  await page.locator("#order-note").fill(note);
  await page.getByRole("button", { name: "送出現金訂單" }).click();
  await page.waitForURL(/\/order\/[A-Za-z0-9_-]+$/, { timeout: 45000 });
  await page.getByText("合計 $440").waitFor();
  ok("客人訂單頁顯示體驗品名", (await page.locator("#order-body").innerText()).includes(title));
  const order = await waitFor(async () => (await api("GET", "/online-orders")).orders.find((o) => o.note === note), "POS 拉到單");
  const brewLine = order.lines.find((line) => line.item_id === brew.id);
  ok("POS 線上單：品名帶體驗、品項是原手沖咖啡", brewLine?.name?.startsWith(`${title}・`) === true, JSON.stringify(brewLine));
  await api("POST", `/online-orders/${order.id}/cancel`);
  ok("頁面無 JS 例外", errors.length === 0, errors.join(" | "));
} catch (error) {
  ok("流程例外", false, String(error));
  await page.screenshot({ path: join(SHOTS, "zz-error.png"), fullPage: true }).catch(() => {});
} finally {
  await api("PUT", `/online-order/experiences/${card.id}`, { ...card, id: undefined, is_active: false }).catch(() => {});
  // 加購只推兩樣：本輪的咖啡／甜點角色要拿掉，否則下一輪會被這次的戚風佔掉名額。
  for (const id of [latte.id, cake.id]) {
    await api("PUT", `/online-order/menu-items/${id}/presentation`, role(null)).catch(() => {});
  }
  await browser.close();
}
const failed = results.filter((pass) => !pass).length;
console.log(`\n${results.length - failed}/${results.length} PASS；截圖 ${SHOTS}`);
process.exit(failed ? 1 : 0);
