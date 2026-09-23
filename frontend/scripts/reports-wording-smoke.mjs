// 報表白話用詞煙霧（2026-09-23）：購物金四張與「現金對帳」不再出現會計／統計術語與英文 Session。
// 逐張切過去，檢查新用詞在、舊用詞不在，並各拍一張截圖。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 node scripts/reports-wording-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";
import { openReport } from "./_reports.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "reports-wording");
mkdirSync(SHOTS, { recursive: true });

const CHECKS = [
  {
    report: "今日營運",
    shot: "00-dashboard.png",
    present: ["送出的購物金", "客人用掉的購物金"],
    absent: /兌付/,
  },
  {
    report: "現金對帳",
    shot: "01-daily-cash.png",
    present: ["每次開帳", "開帳編號", "客人用掉的購物金（參考）"],
    absent: /session|兌付/i,
  },
  {
    report: "客人還沒用的購物金",
    shot: "02-liability.png",
    present: ["客人還沒用掉的總額", "放了多久", "未滿 30 天", "超過一年"],
    absent: /兌付|帳齡|負債健康比/,
  },
  {
    report: "購物金發出與使用",
    shot: "03-flows.png",
    present: ["送出去", "客人用掉"],
    absent: /兌付|流量|淨變化/,
  },
  {
    report: "購物金划不划算",
    shot: "04-effectiveness.png",
    present: ["選購物金的比例", "平均多送幾成", "每送 1,000 元購物金，店家淨賺／賠多少", "推估"],
    absent: /代理|α|alpha|beta|delta|估計值|溢價|選用率/,
  },
  {
    report: "購物金帳對不對",
    shot: "05-reconciliation.png",
    present: ["每位會員的餘額對不對", "客人還沒用掉的總額"],
    absent: /帳目核對|總負債|不一致帳戶/,
  },
];

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });

  for (const check of CHECKS) {
    await openReport(page, check.report);
    await page.getByText(check.present[0], { exact: true }).first().waitFor({ timeout: 15000 });
    const text = await page.locator("main").innerText();
    const missing = check.present.filter((word) => !text.includes(word));
    ok(`${check.report}：新用詞都在`, missing.length === 0, missing.join("、"));
    const leaked = text.match(check.absent);
    ok(`${check.report}：舊術語不再出現`, leaked === null, leaked?.[0] ?? "");
    await page.screenshot({ path: join(SHOTS, check.shot), fullPage: true });
  }

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  ok("流程例外", false, String(error));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exitCode = failed > 0 ? 1 : 0;
