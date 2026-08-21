// 手冊 09e：登記手開紙本備用發票（docs/36）——字軌用完或平台故障時，店員當場開紙本給客人，
// 事後把那張紙的號碼登記回系統。
//
// **為什麼需要這個功能**：發票開立失敗的單一旦離開 POS 完成畫面就再也找不到，
// 交易紀錄預設又只看今天。所以有「只看未開立發票的交易（不限今日）」這個篩選，
// 手冊必須先教會店員找到它。
//
// 造出「未開立」狀態的方法：用 API 建一筆單但**不呼叫開立端點**——`POST /sales` 只建
// PENDING 發票，送平台是 POS 另外呼叫 `/einvoice/sales/{id}/issue`。
import {
  BASE, apiJson, apiLogin, login, makeShot, newBrowser, note, shotsDir, withSettings,
} from "./_lib.mjs";

const dir = shotsDir("09e-manual-paper-invoice");
const shot = makeShot(dir);

await withSettings(["einvoice_enabled"], async () => {
  const token = await apiLogin();
  await apiJson(token, "PATCH", "/api/v1/settings", { einvoice_enabled: true });

  // 開帳（結帳收現需要開帳中的班別）
  const current = await apiJson(token, "GET", "/api/v1/cash-sessions/current");
  let openedHere = false;
  if (current.status !== 200 || !current.json?.id) {
    await apiJson(token, "POST", "/api/v1/cash-sessions/open", { opening_float: "30000" });
    openedHere = true;
  }

  // 建一筆**停在未開立**的交易
  const stock = await apiJson(token, "GET", "/api/v1/serialized-items?status=IN_STOCK&limit=5");
  const item = (Array.isArray(stock.json) ? stock.json : []).find((i) => Number(i.listed_price) > 0);
  if (!item) throw new Error("沒有可售庫存");
  const sale = await apiJson(
    token, "POST", "/api/v1/sales",
    {
      lines: [{ line_type: "SERIALIZED", item_code: item.item_code }],
      tenders: [{ tender_type: "CASH", amount: String(item.listed_price) }],
      expected_einvoice_enabled: true,
    },
    { "Idempotency-Key": `manual-paper-${Date.now()}` },
  );
  if (sale.status !== 201) throw new Error(`結帳失敗 ${sale.status}：${JSON.stringify(sale.json)}`);
  note(`已建立一筆停在「未開立」的交易 #${sale.json.id}（金額 ${sale.json.total}）`);

  const { browser, page } = await newBrowser({ width: 1440, height: 1000 });
  await login(page);

  // ══ 一、找到那筆未開立的交易 ══
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);
  await shot(page, "sales-default", { content: true });

  await page.locator('label:has-text("只看未開立發票的交易") input').check();
  await page.waitForTimeout(1800);
  await shot(page, "pending-only", { content: true });
  note("「只看未開立」不限日期——開立失敗的單可能是好幾天前的，用今天的日期範圍找不到");

  // ══ 二、登記手開發票 ══
  await page.locator(`button[aria-label="登記銷售 ${sale.json.id} 的手開發票"]`).click();
  await page.waitForSelector('[aria-label="登記手開發票"]', { timeout: 8000 });
  await page.waitForTimeout(500);
  await shot(page, "dialog-empty", { locator: '[aria-label="登記手開發票"]' });

  // 號碼每次執行都不同——同店同號碼只能登記一次
  const serial = String(Date.now() % 100000000).padStart(8, "0");
  await page.fill('input[aria-label="發票號碼"]', `PE${serial}`);
  await page.fill('input[aria-label="開立日期"]', new Date().toISOString().slice(0, 10));
  await page.fill('input[aria-label="隨機碼"]', "4321");
  await page.fill('input[aria-label="事由"]', "字軌用完，當場開紙本備用發票給客人");
  await shot(page, "dialog-filled", { locator: '[aria-label="登記手開發票"]' });

  await page.locator('[aria-label="登記手開發票"] button:has-text("確認登記")').click();
  await page.waitForTimeout(2500);
  await shot(page, "registered", { content: true });
  note("登記後該筆的發票來源標為「手開紙本」，且原本待送平台的佇列列轉為已取消——" +
    "不做這一步的話，字軌恢復後有人按重試，平台就會再開一張，同一筆交易兩張發票");

  // ══ 三、手開紙本的退貨：系統不代管折讓 ══
  //
  // **登記完那筆就不再是「未開立」了**，所以會從「只看未開立」的清單消失——
  // 要先把篩選取消才找得到它。第一版沒取消，`click()` 找不到列而失敗，
  // 但我寫了 `.catch(() => {})` 把錯誤吞掉，於是**截到的是交易列表卻配上退貨的說明**。
  // 吞例外正是讓它躲過檢查的原因；這裡一律不吞。
  await page.locator('label:has-text("只看未開立發票的交易") input').uncheck();
  await page.waitForTimeout(1800);
  const row = page.locator(`tr:has-text("#${sale.json.id}")`).first();
  await row.waitFor({ timeout: 15000 });
  await shot(page, "row-manual-paper", { locator: `tr:has-text("#${sale.json.id}")` });
  note("該筆在列表上的發票狀態已標為手開紙本");

  await row.locator('button:has-text("退貨")').click();
  await page.waitForSelector('[aria-label="退貨"]', { timeout: 10000 });
  await page.waitForTimeout(1500);
  // 只拍對話框：整頁截圖裡它只佔一小角，手冊上看不清楚字
  await shot(page, "return-manual-paper", { locator: '[aria-label="退貨"] .pos-dialog, [aria-label="退貨"]' });
  note("手開紙本的退貨：平台上沒有這張發票，系統不代管折讓——" +
    "畫面會要求店長依國稅局程序處理紙本，並勾選確認後才能退");

  await browser.close();
  if (openedHere) {
    const s = await apiJson(token, "GET", "/api/v1/cash-sessions/current");
    if (s.json?.id) {
      await apiJson(token, "POST", `/api/v1/cash-sessions/${s.json.id}/close`, {
        counted_amount: String(s.json.expected_amount ?? "30000"),
      });
    }
  }
});
