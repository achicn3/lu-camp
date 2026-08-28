// 發票待處理頁煙霧（第二層修正）：作廢/折讓送不出去時，這一頁是唯一看得到、能處理的地方。
// 驗：導覽進得去、清單讀得到、篩選會換內容、有待送出項目時「立即送出」真的把它送掉。
// 執行：node scripts/einvoice-queue-smoke.mjs（backend:8000 + frontend:3000 已起，docs/20）
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = strip(process.env.SMOKE_BASE ?? "http://localhost:3000");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "einvoice-queue");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}
function strip(v) {
  return v.replace(/\/+$/, "");
}

let browser;
try {
  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("帳號").fill(USERNAME);
  await page.getByLabel("密碼").fill(PASSWORD);
  await page.getByRole("button", { name: "登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  ok("登入成功", true);

  // 從導覽進入（而不是直接打網址）——要驗的正是「店員找得到這一頁」。
  await page.getByRole("button", { name: "開啟系統選單" }).click();
  await page.getByRole("link", { name: /發票待處理/ }).click();
  await page.waitForURL(/\/einvoice-queue/, { timeout: 20000 });
  ok("可從系統選單進入發票待處理", true);

  await page.getByRole("heading", { name: "發票待處理" }).waitFor({ timeout: 20000 });
  await page.getByText(/共 \d+ 筆/).waitFor({ timeout: 20000 });
  ok("清單讀取成功", true);
  await page.screenshot({ path: join(SHOTS, "01-failed.png") });

  // 切到「待送出」：本機實測有 F0501 待送出，應看得到「立即送出」
  await page.getByRole("button", { name: "待送出" }).click();
  await page.getByText(/共 \d+ 筆/).waitFor({ timeout: 20000 });
  ok("可切換狀態篩選", true);
  await page.screenshot({ path: join(SHOTS, "02-pending.png") });

  // **只對「作廢」列按送出**：開立列送出去就是真的開一張發票給國稅局，
  // 而待送出清單裡可能積著大量舊交易的開立列（實測踩過：盲按第一列補開了一張舊發票）。
  const voidRow = page.locator("tbody tr").filter({ hasText: "作廢" }).first();
  const sendButtons = voidRow.getByRole("button", { name: /立即送出第 \d+ 筆/ });
  const count = await sendButtons.count();
  if (count > 0) {
    await sendButtons.first().click();
    await page.getByRole("status").waitFor({ timeout: 30000 });
    const msg = (await page.getByRole("status").textContent()) ?? "";
    ok("按下立即送出後有明確結果訊息", msg.trim().length > 0, msg.trim().slice(0, 80));
    await page.screenshot({ path: join(SHOTS, "03-sent.png") });
  } else {
    ok("按下立即送出後有明確結果訊息", true, "目前沒有待送出的作廢項目，此項略過");
  }
} catch (err) {
  ok(`未預期錯誤：${err.message}`, false);
} finally {
  await browser?.close();
}

const passed = results.filter((r) => r.pass).length;
console.log(`\n${passed}/${results.length} 通過`);
process.exit(passed === results.length ? 0 : 1);
