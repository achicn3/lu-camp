// 手冊品質檢查：多尺寸 RWD、水平捲軸、目錄跳轉、搜尋、燈箱、外部資源、離線開啟、列印樣式。
import { mkdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { chromium } from "playwright";

const FILE = process.argv[2] ?? join(homedir(), "tmp", "lu-camp-manual", "露營二手POS-系統操作手冊.html");
const SHOTS = join(homedir(), "tmp", "lu-camp-manual", "qa");
mkdirSync(SHOTS, { recursive: true });
const url = pathToFileURL(FILE).href;

const raw = readFileSync(FILE, "utf8");
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

// ── 靜態檢查（單一檔案 / 無外部資源）──
const externalRefs = [
  ...raw.matchAll(/<(?:script|link|img|source|iframe)[^>]*\s(?:src|href)="((?!data:|#)[^"]*)"/g),
].map((m) => m[1]).filter((u) => u.trim() !== "");
ok("沒有任何外部資源引用（CSS/JS/字型/圖片）", externalRefs.length === 0, externalRefs.slice(0, 5).join(", "));
ok("沒有未替換的圖片佔位符", !raw.includes("data-img="));
ok("沒有缺圖標記", !raw.includes('class="img-missing"'));
const imgCount = (raw.match(/<img /g) || []).length;
const dataImgCount = (raw.match(/src="data:image\/webp;base64,/g) || []).length;
ok(`所有 <img> 皆為 Base64 內嵌（${dataImgCount}/${imgCount - 1}）`, dataImgCount === imgCount - 1);
const altMissing = [...raw.matchAll(/<img (?![^>]*alt=)[^>]*>/g)].length;
ok("每張圖片都有 alt 文字", altMissing <= 1, `缺 alt：${altMissing}（燈箱佔位圖不計）`);

// 語意檢查（Codex 第四/五輪）：手冊絕不可指示操作者用「正式環境／正式憑證」驗證不可逆的
// 稅務或金流行為。因此凡出現「正式環境」「正式憑證」「正式商店憑證」，同一句必須帶否定語
// （不可／不要／勿／切勿），否則視為危險指引並讓 QA 失敗。
const text = raw.replace(/<[^>]+>/g, "");
const risky = [];
for (const match of text.matchAll(/[^。；\n]*正式(環境|憑證|商店憑證)[^。；\n]*[。；]?/g)) {
  const sentence = match[0];
  if (!/(不可|不要|勿|切勿)/.test(sentence)) risky.push(sentence.trim().slice(0, 80));
}
ok("沒有『用正式環境/正式憑證驗證』的危險指引", risky.length === 0, risky.join(" / "));

const browser = await chromium.launch();
const ctx = await browser.newContext({ locale: "zh-TW" });
const page = await ctx.newPage();
const external = [];
page.on("request", (r) => {
  if (!r.url().startsWith("file://") && !r.url().startsWith("data:")) external.push(r.url());
});
const jsErrors = [];
page.on("pageerror", (e) => jsErrors.push(String(e)));

await page.setViewportSize({ width: 1440, height: 900 });
await page.goto(url, { waitUntil: "load" });
await page.waitForTimeout(2500);
ok("離線開啟無網路請求", external.length === 0, external.slice(0, 3).join(", "));
ok("頁面無 JavaScript 錯誤", jsErrors.length === 0, jsErrors.join(" | "));

// ── RWD：五種寬度檢查水平捲軸 ──
for (const w of [360, 390, 768, 1024, 1440]) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.waitForTimeout(700);
  const overflow = await page.evaluate(() => ({
    doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    offenders: [...document.querySelectorAll("body *")]
      .filter((el) => el.getBoundingClientRect().right > document.documentElement.clientWidth + 2)
      .slice(0, 3)
      .map((el) => el.className || el.tagName),
  }));
  ok(`${w}px 無水平捲軸`, overflow.doc <= 1, `溢出 ${overflow.doc}px ${overflow.offenders.join(",")}`);
  await page.screenshot({ path: join(SHOTS, `rwd-${w}.png`), fullPage: false });
}

// ── 手機版目錄可收合 ──
await page.setViewportSize({ width: 390, height: 780 });
await page.waitForTimeout(500);
await page.click("#menu-toggle");
await page.waitForTimeout(500);
ok("手機版目錄可展開", await page.locator("#sidebar.open").isVisible());
await page.screenshot({ path: join(SHOTS, "mobile-toc.png") });
await page.mouse.click(375, 400);
await page.waitForTimeout(400);
ok("手機版目錄可收合", (await page.locator("#sidebar.open").count()) === 0);

// ── 桌面：目錄跳轉 ──
await page.setViewportSize({ width: 1440, height: 900 });
await page.waitForTimeout(400);
await page.click('.toc-link[data-target="ch-pos"]');
await page.waitForFunction(() => Math.abs(document.getElementById("ch-pos").getBoundingClientRect().top) < 120, null, { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(1200);
const posTop = await page.evaluate(() => document.getElementById("ch-pos").getBoundingClientRect().top);
ok("目錄錨點可跳轉（POS 章節）", Math.abs(posTop) < 120, `top=${Math.round(posTop)}`);
const active = await page.locator(".toc-link.active").first().textContent();
ok("目錄會標示目前章節", (active ?? "").includes("POS"), active?.trim());
await page.screenshot({ path: join(SHOTS, "desktop-toc-active.png") });

// ── 搜尋 ──
await page.fill("#search-input", "退貨");
await page.waitForTimeout(600);
const hits = await page.locator("#search-results a").count();
ok("搜尋「退貨」有結果", hits > 0, `${hits} 筆`);
await page.screenshot({ path: join(SHOTS, "search.png") });
await page.locator("#search-results a").first().click();
await page.waitForTimeout(900);
ok("搜尋結果可跳轉", (await page.locator("#search-results.open").count()) === 0);

await page.fill("#search-input", "zzz不存在zzz");
await page.waitForTimeout(500);
ok("搜尋無結果時顯示提示", (await page.locator("#search-results .sr-empty").count()) === 1);
await page.fill("#search-input", "");
await page.keyboard.press("Escape");

// ── 燈箱 ──
await page.goto(`${url}#ch-acquisition`, { waitUntil: "load" });
await page.waitForTimeout(1500);
const firstImg = page.locator(".chapter#ch-acquisition .shot img").first();
await firstImg.scrollIntoViewIfNeeded();
const beforeScroll = await page.evaluate(() => window.scrollY);
await firstImg.click();
await page.waitForTimeout(700);
ok("點圖片可放大", await page.locator("#lightbox.open").isVisible());
await page.screenshot({ path: join(SHOTS, "lightbox.png") });
await page.keyboard.press("Escape");
await page.waitForTimeout(700);
const afterScroll = await page.evaluate(() => window.scrollY);
ok("關閉放大後回到原閱讀位置", Math.abs(afterScroll - beforeScroll) < 60, `${beforeScroll} → ${afterScroll}`);

// ── 鍵盤可操作 ──
await page.keyboard.press("Tab");
const focused = await page.evaluate(() => document.activeElement?.className || document.activeElement?.tagName);
ok("可用鍵盤 Tab 導覽", Boolean(focused), String(focused));

// ── 圖片實際載入 ──
const broken = await page.evaluate(async () => {
  const imgs = [...document.querySelectorAll(".shot img")];
  imgs.forEach((i) => i.setAttribute("loading", "eager"));
  await new Promise((r) => setTimeout(r, 4000));
  return imgs.filter((i) => i.complete && i.naturalWidth === 0).length;
});
ok("所有截圖皆可正常顯示", broken === 0, `破圖 ${broken} 張`);

// ── 列印樣式 ──
await page.emulateMedia({ media: "print" });
await page.waitForTimeout(600);
const printState = await page.evaluate(() => ({
  sidebar: getComputedStyle(document.getElementById("sidebar")).display,
  topbar: getComputedStyle(document.querySelector(".topbar")).display,
  width: document.documentElement.scrollWidth - document.documentElement.clientWidth,
}));
ok("列印模式隱藏目錄與工具列", printState.sidebar === "none" && printState.topbar === "none");
ok("列印模式無水平溢出", printState.width <= 1, `${printState.width}px`);
await page.pdf({ path: join(SHOTS, "print-preview.pdf"), format: "A4", printBackground: true }).catch((e) => {
  console.log("   （PDF 產生略過：" + e.message + "）");
});
await page.emulateMedia({ media: "screen" });

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
if (failed.length) console.log("未通過：" + failed.map((f) => f.name).join("、"));
await browser.close();
process.exit(failed.length ? 1 : 0);
