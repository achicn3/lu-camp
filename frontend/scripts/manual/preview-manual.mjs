// 產生手冊各章節的預覽截圖，供人工目視檢查排版。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { chromium } from "playwright";

const FILE = join(homedir(), "tmp", "lu-camp-manual", "露營二手POS-系統操作手冊.html");
const OUT = join(homedir(), "tmp", "lu-camp-manual", "qa");
mkdirSync(OUT, { recursive: true });
const url = pathToFileURL(FILE).href;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
for (const [anchor, name] of [
  ["ch-acquisition", "chapter-acquisition"],
  ["ch-pos", "chapter-pos"],
  ["ch-faq", "chapter-faq"],
  ["ch-coverage", "chapter-coverage"],
  ["ch-flows", "chapter-flows"],
]) {
  await page.goto(`${url}#${anchor}`, { waitUntil: "load" });
  await page.waitForTimeout(2500);
  await page.evaluate(() => {
    document.querySelectorAll(".shot img").forEach((i) => i.setAttribute("loading", "eager"));
  });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: join(OUT, `${name}.png`) });
  console.log(`📸 ${name}.png`);
}
await browser.close();
