// 收購佇列 I2 煙霧（docs/42）：報到收件（新建賣方、點清 3 件；收件單送收據機印兩份）→ 估價兩列
// （五折自動帶收購價、六折紅字）→ 補印一份 → 估完送叫號 → 逐列處置（收 1 張、記已交還）→ 回佇列；
// 另驗代理連不上時：照樣進估價頁並提示補印。硬體代理以 Playwright 攔截模擬（真機列印由代理測試守）。
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

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
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
  await page.getByRole("heading", { name: "收購佇列" }).waitFor();

  // ① 報到：新建賣方、點清 3 件
  await page.getByRole("button", { name: /建立新賣方/ }).click();
  await page.getByLabel("姓名", { exact: true }).fill(SELLER);
  await page.getByLabel("手機", { exact: true }).fill(uniquePhone());
  await page.getByLabel("身分證字號", { exact: true }).fill(validNationalId());
  await page.getByRole("button", { name: "建立並選取" }).click();
  await page.getByLabel("實收件數").fill("3");
  await page.screenshot({ path: join(SHOTS, "01-checkin.png"), fullPage: true });
  await page.getByRole("button", { name: "報到，發號碼" }).click();
  await page.waitForURL(/\/acquisition\/intake\/\d+/);
  await page.getByText("收件單已送出列印（兩份）。").waitFor();
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

  // ② 估價第一列：黑色折疊椅 ×2、原價 1000、五折
  const form = page.getByRole("form", { name: "新增一列" });
  await form.getByLabel("商品簡稱").fill("黑色折疊椅");
  await form.getByLabel("數量").fill("2");
  await form.getByLabel("原價／件").fill("1000");
  await form.getByRole("button", { name: "5折", exact: true }).click();
  const listed = await form.getByLabel("預計售價／件").inputValue();
  const deal = await form.getByLabel("成交收購價／件").inputValue();
  ok("五折自動帶預計售價 500、建議收購價當成交價", listed === "500" && Number(deal) > 0, `${listed}／${deal}`);
  ok("成色沒點時顯示依折數推斷", (await form.getByLabel("成色").innerText()).includes("依折數推斷"));
  await form.getByRole("button", { name: "存這一件" }).click();
  await page.getByRole("cell", { name: /黑色折疊椅/ }).waitFor();

  // ② 第二列：露營桌、原價 3000、六折 → 紅字
  const form2 = page.getByRole("form", { name: "新增一列" });
  await form2.getByLabel("商品簡稱").fill("露營桌");
  await form2.getByLabel("原價／件").fill("3000");
  await form2.getByRole("button", { name: "6折", exact: true }).click();
  ok("六折出現新品紅字提醒", (await form2.locator(".acq-near-new").count()) === 1);
  await page.screenshot({ path: join(SHOTS, "02-estimating.png"), fullPage: true });
  await form2.getByRole("button", { name: "存這一件" }).click();
  await page.getByRole("cell", { name: /露營桌/ }).waitFor();
  const summary = await page.locator(".intake-summary").innerText();
  ok("已估 2 項 3 件、狀態估價中", summary.includes("已估 2 項 3 件") && summary.includes("估價中"), summary.replace(/\n/g, " "));

  // ③ 估完送叫號 → 逐列處置
  await page.getByRole("button", { name: "估完，送去叫號" }).click();
  await page.getByText("待確認（等叫號）").waitFor();
  await page.getByLabel("第 1 列處置").first().selectOption("ACCEPTED");
  await page.getByLabel("第 1 列接受件數").fill("1");
  await page.getByText("沒收的 1 件已交還客人").click();
  await page.locator(".intake-disposition").first().getByRole("button", { name: "儲存" }).click();
  await page.getByText(/接受 1 件/).waitFor();
  await page.getByLabel("第 2 列處置").first().selectOption("ACCEPTED");
  await page.locator(".intake-disposition").nth(1).getByRole("button", { name: "儲存" }).click();
  await page.getByText(/接受 2 件/).waitFor();
  ok("部分接受後接受件數與收購總額更新", (await page.locator(".intake-summary").innerText()).includes("接受 2 件"));
  await page.screenshot({ path: join(SHOTS, "03-confirming.png"), fullPage: true });

  // 回佇列
  await page.getByRole("link", { name: "回收購佇列" }).click();
  await page.getByRole("heading", { name: "收購佇列" }).waitFor();
  const row = page.locator("tr", { hasText: SELLER });
  await row.waitFor();
  ok("佇列列出這一批、狀態待確認", (await row.innerText()).includes("待確認"), (await row.innerText()).replace(/\s+/g, " "));
  await page.screenshot({ path: join(SHOTS, "04-queue.png"), fullPage: true });

  // 代理連不上：照樣進估價頁，並提示補印（號碼已登記，不能因印表機卡住現場）
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
  await page.getByRole("link", { name: "回收購佇列" }).click();
  await page.getByRole("heading", { name: "收購佇列" }).waitFor();

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
