// 收購佇列煙霧（docs/42 §13 快速估價）：報到收件（新建賣方、收購 3 件；收件單送收據機印兩份）→ 補印一份
// → 3 個收購價輸入框、Enter 跳下一件 → 詳細補名稱 → 估完 → 交給客人勾選（3 號不賣）→ 同一台平板簽署
// （回上一頁重勾、內容跟著換；選購物金）→ 後端處置正確。硬體代理以 Playwright 攔截模擬（真機列印由代理測試守）。
// 需 backend + frontend 已起、已 seed（dev-manager）。執行：node scripts/intake-queue-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "intake-queue");
const RUN = String(Date.now()).slice(-6);
const SELLER = `佇列賣家-${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function drawSignature(target) {
  const canvas = target.locator("canvas.kiosk-sign-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("找不到簽名畫布");
  const pts = [[0.15, 0.5], [0.3, 0.25], [0.45, 0.7], [0.6, 0.3], [0.75, 0.6], [0.85, 0.4]];
  await target.mouse.move(box.x + box.width * pts[0][0], box.y + box.height * pts[0][1]);
  await target.mouse.down();
  for (const [fx, fy] of pts.slice(1)) {
    await target.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 12 });
  }
  await target.mouse.up();
}

const browser = await chromium.launch();
// 平板尺寸（店主會在平板上操作，docs/42 §13）。
const page = await browser.newPage({ viewport: { width: 820, height: 1180 }, hasTouch: true });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "dev-manager", password: "dev-test-123456" }),
  });
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  const prints = [];
  let agentDown = false;
  await page.route("**/print/**", async (route) => {
    if (agentDown) return route.abort("connectionrefused");
    prints.push({ url: route.request().url(), body: route.request().postDataJSON() });
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
  });
  await page.goto(`${BASE}/acquisition/intake`, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "排隊收購" }).waitFor();

  // ① 報到：新建賣方、點清 3 件
  await page.getByRole("button", { name: /建立新賣方/ }).click();
  await page.getByLabel("姓名", { exact: true }).fill(SELLER);
  await page.getByLabel("手機", { exact: true }).fill(uniquePhone());
  await page.getByLabel("身分證字號", { exact: true }).fill(validNationalId());
  await page.getByRole("button", { name: "建立並選取" }).click();
  await page.getByLabel("收購幾件").fill("3");
  await page.screenshot({ path: join(SHOTS, "01-checkin.png"), fullPage: true });
  await page.getByRole("button", { name: "報到，發號碼" }).click();
  await page.waitForURL(/\/acquisition\/intake\/\d+/);
  await page.getByText("收件單已送出列印（兩份）。").waitFor();
  await page.waitForURL((url) => !url.search.includes("print="), { timeout: 5_000 }).catch(() => {});
  ok("印完把 ?print=new 拿掉（重新整理不會再印）", !page.url().includes("print="), page.url());
  const title = await page.locator("h1").innerText();
  ok("報到後直接進估價頁、有 A 編號", /A\d{3}/.test(title) && title.includes(SELLER), title);
  const label = /A\d{3}/.exec(title)?.[0];
  const first = prints[0];
  ok(
    "收件單送收據機、印兩份、號碼與條碼正確",
    prints.length === 1 &&
      first.url.endsWith("/print/intake-slip") &&
      first.body.copies === 2 &&
      first.body.label === label &&
      /^IN\d{6}$/.test(first.body.slip_code) &&
      first.body.seller_name === SELLER &&
      first.body.declared_item_count === 3,
    JSON.stringify(first?.body),
  );
  await page.getByRole("button", { name: "補印一份" }).click();
  await page.getByText("收件單已送出列印。").waitFor();
  ok("補印一份", prints.length === 2 && prints[1].body.copies === 1);

  // ② 快速估價（docs/42 §13）：照件數建好 3 個輸入框；只填收購價，按 Enter 跳下一件
  const inputs = page.getByRole("textbox", { name: /號 收購價$/ });
  ok("報到 3 件就有 3 個收購價輸入框", (await inputs.count()) === 3);
  await page.getByLabel("1 號 收購價").click();
  await page.keyboard.type("300");
  await page.keyboard.press("Enter");
  ok("按 Enter 跳到 2 號", await page.getByLabel("2 號 收購價").evaluate((el) => el === document.activeElement));
  await page.keyboard.type("500");
  await page.keyboard.press("Enter");
  await page.keyboard.type("200");
  await page.keyboard.press("Enter");
  await page.getByText(/已填 3／3 件/).waitFor();
  // 2 號展開詳細，補名稱
  await page.getByRole("button", { name: "2 號 詳細" }).click();
  const detailForm = page.getByRole("form", { name: /修改第 2 列/ });
  await detailForm.getByLabel("商品簡稱").fill("露營桌");
  await page.screenshot({ path: join(SHOTS, "02-quick-estimate.png") });
  await detailForm.getByRole("button", { name: "儲存詳細" }).click();
  await detailForm.waitFor({ state: "detached" });
  const batchId = Number(/\/acquisition\/intake\/(\d+)/.exec(page.url())?.[1]);
  const token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
  const batchApi = async () =>
    (await fetch(`${API}/api/v1/intake-batches/${batchId}`, { headers: { Authorization: `Bearer ${token}` } })).json();
  let saved = await batchApi();
  ok(
    "收購價與詳細都存進後端",
    JSON.stringify(saved.lines.map((l) => [l.deal_cost, l.short_name])) ===
      JSON.stringify([["300", "第 1 件"], ["500", "露營桌"], ["200", "第 3 件"]]),
    JSON.stringify(saved.lines.map((l) => [l.deal_cost, l.short_name])),
  );

  // ③ 估完 → 交給客人勾選（3 號不賣）
  await page.getByRole("button", { name: "估完，給客人確認" }).click();
  await page.getByRole("button", { name: "交給客人勾選" }).click();
  const sheet = page.getByRole("dialog", { name: /確認要賣的商品/ });
  await sheet.waitFor();
  ok("客人畫面不顯示成本毛利售價", !/毛利|預計售價|成本|建議/.test(await sheet.innerText()));
  await sheet.getByRole("checkbox", { name: /3 號/ }).uncheck();
  ok("取消勾選後合計即時更新", /共 2 件.*\$800/.test(await sheet.getByRole("status").innerText()));
  await page.screenshot({ path: join(SHOTS, "03-customer-checklist.png") });
  ok("勾選頁只有「確認」一個按鈕", (await sheet.getByRole("button").allInnerTexts()).join() === "確認");
  await sheet.getByRole("button", { name: "確認" }).click();
  // ④ 同一台平板直接進簽署頁；回上一頁重勾，再進來是新內容
  let signing = page.getByRole("dialog", { name: "簽署切結書" });
  await signing.waitFor();
  ok("確認後直接進簽署頁、合計 $800", /\$800/.test(await signing.getByRole("button", { name: /現金/ }).innerText()));
  await signing.getByRole("button", { name: /回上一頁/ }).click();
  await sheet.waitFor();
  ok("回上一頁保留剛才的勾選（3 號仍不賣）", !(await sheet.getByRole("checkbox", { name: /3 號/ }).isChecked()));
  await sheet.getByRole("checkbox", { name: /1 號/ }).uncheck();
  await sheet.getByRole("button", { name: "確認" }).click();
  await signing.waitFor();
  ok("重勾後簽署頁換成新內容（只剩 $500）", /\$500/.test(await signing.getByRole("button", { name: /現金/ }).innerText()));
  await signing.getByRole("button", { name: /回上一頁/ }).click();
  await sheet.getByRole("checkbox", { name: /1 號/ }).check();
  await sheet.getByRole("button", { name: "確認" }).click();
  await signing.waitFor();
  ok("再勾回來又是 $800", /\$800/.test(await signing.getByRole("button", { name: /現金/ }).innerText()));
  await signing.getByRole("checkbox", { name: /同意/ }).check();
  const creditText = await signing.getByRole("button", { name: /購物金/ }).innerText();
  ok("購物金寫「多拿 X% 購物金」、不寫多得多少錢", /多拿 [\d.]+% 購物金/.test(creditText) && !creditText.includes("多得"), creditText.replace(/\n/g, " "));
  await signing.getByRole("button", { name: /購物金/ }).click();
  await drawSignature(page);
  await page.screenshot({ path: join(SHOTS, "03b-tablet-signing.png") });
  await signing.getByRole("button", { name: "確認並送出" }).click();
  await page.getByText(/請把平板交還給店員/).waitFor();
  ok("簽完請客人交還店員、寫出選了購物金", (await page.locator(".intake-customer-done").innerText()).includes("購物金"));
  await page.getByRole("button", { name: "交還店員" }).click();
  await page.getByText(/客人已簽名，選擇拿購物金/).waitFor({ timeout: 8000 });
  ok("店員畫面：客人已簽名、選購物金", true);
  saved = await batchApi();
  ok(
    "後端：1、2 號要賣，3 號客人不賣且已交還",
    JSON.stringify(saved.lines.map((l) => [l.disposition, l.returned_to_customer])) ===
      JSON.stringify([["ACCEPTED", false], ["ACCEPTED", false], ["CUSTOMER_KEPT", true]]) &&
      saved.accepted_total === "800",
    JSON.stringify(saved.lines.map((l) => l.disposition)),
  );
  ok("店員畫面標出 3 號不賣", (await page.locator(".intake-confirm-list li.is-kept").innerText()).includes("3 號"));
  await page.screenshot({ path: join(SHOTS, "04-confirmed.png"), fullPage: true });

  // 客人全部不賣：紅色「確認都不賣」→ 整批取消、每件已交還
  {
    const res = await fetch(`${API}/api/v1/intake-batches`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ contact_id: saved.contact_id, declared_item_count: 2, prefill_lines: true }),
    });
    const other = await res.json();
    for (const [i, line] of other.lines.entries()) {
      await fetch(`${API}/api/v1/intake-batches/${other.id}/lines/${line.id}`, {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ deal_cost: String(100 * (i + 1)) }),
      });
    }
    await fetch(`${API}/api/v1/intake-batches/${other.id}/ready`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    await page.goto(`${BASE}/acquisition/intake/${other.id}`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "交給客人勾選" }).click();
    const declineSheet = page.getByRole("dialog", { name: /確認要賣的商品/ });
    await declineSheet.getByRole("checkbox", { name: /1 號/ }).uncheck();
    await declineSheet.getByRole("checkbox", { name: /2 號/ }).uncheck();
    ok("全部取消勾選：只剩紅色「確認都不賣」", (await declineSheet.getByRole("button").allInnerTexts()).join() === "確認都不賣");
    await page.screenshot({ path: join(SHOTS, "07-decline.png") });
    await declineSheet.getByRole("button", { name: "確認都不賣" }).click();
    await page.getByText("好的，這次都不賣。").waitFor();
    await page.getByRole("button", { name: "交還店員" }).click();
    const after = await (await fetch(`${API}/api/v1/intake-batches/${other.id}`, { headers: { Authorization: `Bearer ${token}` } })).json();
    ok(
      "都不賣：整批取消、每件記客人不賣且已交還",
      after.status === "CANCELLED" && after.cancel_reason === "客人確認都不賣" && after.lines.every((l) => l.disposition === "CUSTOMER_KEPT" && l.returned_to_customer),
      `${after.status} ${after.cancel_reason}`,
    );
  }

  // 代理連不上：照樣進估價頁並提示補印
  await page.goto(`${BASE}/acquisition/intake`, { waitUntil: "networkidle" });
  agentDown = true;
  await page.getByRole("button", { name: /建立新賣方/ }).click();
  await page.getByLabel("姓名", { exact: true }).fill(`${SELLER}-2`);
  await page.getByLabel("手機", { exact: true }).fill(uniquePhone());
  await page.getByLabel("身分證字號", { exact: true }).fill(validNationalId());
  await page.getByRole("button", { name: "建立並選取" }).click();
  await page.getByRole("button", { name: "報到，發號碼" }).click();
  await page.waitForURL(/\/acquisition\/intake\/\d+/);
  await page.getByText(/收件單沒有印出來/).waitFor();
  ok("代理連不上時仍進估價頁並提示補印", true);
  await page.screenshot({ path: join(SHOTS, "06-print-failed.png"), fullPage: true });
  agentDown = false;
  await page.getByRole("link", { name: "回排隊清單" }).click();
  await page.getByRole("heading", { name: "排隊收購" }).waitFor();
  const pendingRow = page.locator("tr", { hasText: `${SELLER}-2` });
  ok("還沒估的批次只給「估價」按鈕", (await pendingRow.getByRole("link").allInnerTexts()).join() === "估價");
  ok("還沒估的批次在清單上明講「還差 1 件沒估」", (await pendingRow.locator(".intake-missing").innerText()).includes("還差 1 件沒估"));

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: "networkidle" });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  ok("手機寬度不會整頁橫向捲動", !overflow);
  await page.screenshot({ path: join(SHOTS, "05-mobile.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  console.log(String(error));
  process.exitCode = 1;
} finally {
  await browser.close();
}
