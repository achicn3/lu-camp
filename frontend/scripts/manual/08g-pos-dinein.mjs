// 手冊 08g：餐飲內用／外帶（docs/35）——設定桌號 → POS 選內用/外帶 → 桌號 → 結帳出餐單。
//
// 這一支的重點在**先後順序**：桌號清單沒設好之前，POS 的「內用」按鈕是**停用**的。
// 手冊必須先拍到「還沒設定桌號」那個狀態，店員才知道為什麼按不下去。
import { BASE, apiJson, apiLogin, login, makeShot, newBrowser, note, shotsDir, withSettings } from "./_lib.mjs";

const dir = shotsDir("08g-pos-dinein");
const shot = makeShot(dir);

await withSettings(["dine_in_tables", "print_kitchen_ticket"], async () => {
  const token = await apiLogin();
  // 從「還沒設定桌號」開始——那是新店家的真實起點
  await apiJson(token, "PATCH", "/api/v1/settings", {
    dine_in_tables: [],
    print_kitchen_ticket: true,
  });

  const { browser, page } = await newBrowser({ width: 1440, height: 1000 });
  await login(page);

  // ══ 一、設定桌號（內用的前提）══
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  await shot(page, "settings-no-tables", { locator: ".dinein-card" });
  note("還沒設定桌號時，設定頁明講「POS 不能選內用」");

  for (const table of ["A1", "A2", "B1"]) {
    await page.fill('input[aria-label="新增桌號"]', table);
    await page.click('button:has-text("新增桌號")');
    await page.waitForTimeout(400);
  }
  await shot(page, "settings-tables-added", { locator: ".dinein-card" });
  await page.locator('.dinein-card button:has-text("儲存")').first().click();
  await page.waitForTimeout(1500);
  await shot(page, "settings-saved", { locator: ".dinein-card" });
  note("桌號清單的順序就是 POS 上按鈕的順序；移除桌號不影響已結帳的歷史交易");

  // ══ 二、POS：內用 ══
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);
  await shot(page, "pos-empty", { locator: ".pos-left" });

  const addMenu = async (name, qty) => {
    await page.locator(`.pos-menu-tile:has-text("${name}")`).first().click();
    await page.waitForSelector(".pos-qty-dialog", { timeout: 8000 });
    await page.waitForTimeout(300);
    await page.fill('input[aria-label="數量"]', String(qty));
    await page.click('.pos-qty-dialog button:has-text("加入購物車")');
    await page.waitForTimeout(1200);
  };

  await shot(page, "menu-tiles", { locator: ".pos-menu" });
  await addMenu("美式咖啡", 2);
  await addMenu("鬆餅", 1);
  // 內用/外帶面板**只有購物車裡有餐飲品項時才會出現**——純二手的單不該被標成內用
  await shot(page, "dinein-panel", { locator: ".pos-dinein-panel" });
  note("內用/外帶面板只在購物車有餐飲品項時出現；純二手的單不會有這一區");

  await page.locator('.pos-dinein-mode:has-text("內用")').click();
  await page.waitForTimeout(500);
  await shot(page, "dinein-tables", { locator: ".pos-dinein-panel" });
  await page.locator('.pos-dinein-table:has-text("A2")').click();
  await page.waitForTimeout(500);
  await shot(page, "dinein-table-selected", { locator: ".pos-dinein-panel" });

  await page.locator('.field:has-text("實收現金") input').fill("500");
  await page.waitForTimeout(400);
  await shot(page, "dinein-before-checkout", { locator: ".pos-right" });
  await page.click(".pos-checkout");
  await page.waitForTimeout(3000);
  await shot(page, "dinein-done", { content: true });
  note("內用結帳後會列印出餐單（桌號＋餐飲品項），吧台據此出餐");

  // ══ 三、POS：外帶（不必選桌號）══
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);
  await addMenu("拿鐵", 1);
  await page.locator('.pos-dinein-mode:has-text("外帶")').click();
  await page.waitForTimeout(500);
  await shot(page, "takeout-selected", { locator: ".pos-dinein-panel" });
  note("外帶不需要選桌號——選了反而會被擋下");

  await page.locator('.field:has-text("實收現金") input').fill("200");
  await page.waitForTimeout(400);
  await page.click(".pos-checkout");
  await page.waitForTimeout(3000);
  await shot(page, "takeout-done", { content: true });

  await browser.close();
});
