// 手冊 13：簽署紀錄——狀態/類型篩選、分頁、查看簽名證據。
import { BASE, login, makeShot, newBrowser, shotsDir } from "./_lib.mjs";

const dir = shotsDir("13-signing");
const shot = makeShot(dir);
const { browser, page } = await newBrowser();
await login(page);

await page.goto(`${BASE}/signing`, { waitUntil: "networkidle" });
await page.waitForTimeout(1800);
await shot(page, "list", { content: true });
await shot(page, "filters", { locator: ".signing-filters" });

await page.locator('select[aria-label="類型"]').selectOption({ label: "收購切結" });
await page.waitForTimeout(1500);
await shot(page, "filter-kind", { content: true });

await page.locator('select[aria-label="狀態"]').selectOption("");
await page.locator('select[aria-label="類型"]').selectOption("");
await page.waitForTimeout(1500);
await shot(page, "filter-all", { content: true });

const viewBtn = page.locator("table tbody tr button:has-text('查看')").first();
await viewBtn.click();
await page.waitForTimeout(2500);
await shot(page, "evidence", { locator: ".pos-dialog, .signature-dialog, [role='dialog']" });
await page.screenshot({ path: `${dir}/06-evidence-full.png`, fullPage: true });
console.log("   📸 06-evidence-full.png");

await browser.close();
console.log("✅ 13-signing 完成");
