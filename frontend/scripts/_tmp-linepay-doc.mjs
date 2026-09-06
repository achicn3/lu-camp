// 讀 LINE Pay v4 文件：欄位表藏在折疊元件裡，markdown 轉換抓不到。
// 用真瀏覽器載入 → 把所有 details/折疊按鈕展開 → 抽出含 affiliateCards 的段落。
import { chromium } from "playwright";

const URL = process.argv[2];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
try {
  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForTimeout(6000);

  // 展開所有可能的折疊：<details>、aria-expanded=false 的按鈕、常見的 accordion class
  await page.evaluate(() => {
    document.querySelectorAll("details").forEach((d) => (d.open = true));
  });
  for (let round = 0; round < 6; round += 1) {
    const toggles = await page.$$('[aria-expanded="false"]');
    if (toggles.length === 0) break;
    for (const t of toggles) {
      await t.click({ timeout: 1500 }).catch(() => {});
    }
    await page.waitForTimeout(500);
  }
  await page.waitForTimeout(1000);

  const text = await page.evaluate(() => document.body.innerText);
  console.log("=== 全文長度 ===", text.length);
  const hit = text.includes("affiliateCards");
  console.log("=== 含 affiliateCards ===", hit);
  if (hit) {
    const idx = text.indexOf("affiliateCards");
    console.log("=== affiliateCards 前後文 ===");
    console.log(text.slice(Math.max(0, idx - 1500), idx + 3000));
  } else {
    for (const kw of ["merchantReference", "cardType", "MOBILE_CARRIER", "載具"]) {
      const i = text.indexOf(kw);
      console.log(`--- ${kw}: ${i === -1 ? "找不到" : "有"} ---`);
      if (i !== -1) console.log(text.slice(Math.max(0, i - 600), i + 1200));
    }
  }
} finally {
  await browser.close();
}
