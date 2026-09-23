// 首次採購新增商品煙霧：登入 → 建立採購單頁搜尋無結果 → 新增商品（沒有 SKU 欄位）→
// 後端自動產生條碼 → 商品直接加入採購明細。預設以 dev-clerk 驗證店員權限。
// 另驗：建立回應遺失時，重整後還原原內容並以同一冪等鍵重送、不重複建檔。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-clerk";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "general-product");
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const productName = `首次採購測試營繩 ${Date.now().toString().slice(-6)}`;
const recoveryName = `回應遺失測試營繩 ${Date.now().toString().slice(-6)}`;

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  await page.goto(`${BASE}/purchasing/new`, { waitUntil: "networkidle" });
  await page.getByLabel("搜尋一般商品").fill(productName);
  await page.getByText(/查無相符的商品/).waitFor();
  await page.getByRole("button", { name: "＋ 新增商品" }).click();

  if ((await page.getByLabel("一般商品名稱").inputValue()) !== productName) {
    throw new Error("搜尋文字未帶入一般商品名稱");
  }
  if ((await page.getByLabel("一般商品編號").count()) !== 0) {
    throw new Error("採購頁不應再出現 SKU 欄位");
  }
  await page.getByLabel("一般商品售價").fill("280");
  await page.getByLabel("一般商品低庫存提醒點").fill("5");
  await page.screenshot({ path: join(SHOTS, "01-create-general-product.png"), fullPage: true });

  await page.getByRole("button", { name: "建立並加入採購單" }).click();
  const line = page.locator(".pur-lines tbody tr").filter({ hasText: productName });
  await line.waitFor();
  await page.screenshot({ path: join(SHOTS, "02-added-to-purchase-order.png"), fullPage: true });

  // 實際讓後端 commit，但將第一次回應替換成 500：模擬開檔成功後連線中斷。
  // 重整後應還原原 body＋原 Idempotency-Key，重送由後端回放同一商品。
  const recoveryKeys = [];
  let committedRecoveryProduct = null;
  let replayedRecoveryProduct = null;
  await page.route("**/api/v1/catalog-products", async (route) => {
    const request = route.request();
    if (request.method() !== "POST") {
      await route.continue();
      return;
    }
    recoveryKeys.push(request.headers()["idempotency-key"]);
    if (committedRecoveryProduct === null) {
      const response = await route.fetch();
      if (response.status() !== 201) {
        throw new Error(`預期首次建檔 201，實際 ${response.status()}`);
      }
      committedRecoveryProduct = await response.json();
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "模擬回應遺失" }),
      });
      return;
    }
    // 重送：照常送到後端，順手記下回放的商品，確認是同一筆（沒有重複建檔）。
    const replay = await route.fetch();
    replayedRecoveryProduct = await replay.json();
    await route.fulfill({ response: replay });
  });

  await page.getByLabel("搜尋一般商品").fill(recoveryName);
  await page.getByText(/查無相符的商品/).waitFor();
  await page.getByRole("button", { name: "＋ 新增商品" }).click();
  await page.getByLabel("一般商品售價").fill("290");
  await page.getByLabel("一般商品低庫存提醒點").fill("6");
  await page.getByRole("button", { name: "建立並加入採購單" }).click();
  await page.getByText("模擬回應遺失").waitFor();

  await page.reload({ waitUntil: "networkidle" });
  await page.getByText(/上一筆商品有沒有建好還不確定/).waitFor();
  if ((await page.getByLabel("一般商品名稱").inputValue()) !== recoveryName) {
    throw new Error("重整後未還原待確認商品名稱");
  }
  if ((await page.getByLabel("一般商品售價").inputValue()) !== "290") {
    throw new Error("重整後未還原待確認商品售價");
  }
  await page.screenshot({ path: join(SHOTS, "03-restored-pending-product.png"), fullPage: true });
  await page.getByRole("button", { name: "重試並確認建立結果" }).click();

  const recoveredLine = page.locator(".pur-lines tbody tr").filter({ hasText: recoveryName });
  await recoveredLine.waitFor();
  if (replayedRecoveryProduct?.id !== committedRecoveryProduct.id) {
    throw new Error(
      `重試產生不同商品：#${committedRecoveryProduct.id} → #${replayedRecoveryProduct?.id}`,
    );
  }
  if (recoveryKeys.length !== 2 || recoveryKeys[0] !== recoveryKeys[1]) {
    throw new Error("重整後未沿用原 Idempotency-Key");
  }
  await page.screenshot({ path: join(SHOTS, "04-reconciled-product.png"), fullPage: true });

  console.log(
    `✅ 首次採購新增商品與回應遺失復原通過：${productName}；${recoveryName} #${replayedRecoveryProduct.id}`,
  );
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true }).catch(() => {});
  console.error(error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
