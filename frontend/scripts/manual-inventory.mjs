// 手冊用：登入後逐頁擷取「實際 DOM 清單」（標題/按鈕/欄位/表頭/分頁籤）與整頁截圖。
// 純讀取，不改資料。輸出 JSON 到 SMOKE_SHOTS/inventory.json。
import { mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-manual", "inventory");
mkdirSync(SHOTS, { recursive: true });

const ROUTES = [
  ["/", "home"],
  ["/pos", "pos"],
  ["/sales", "sales"],
  ["/cash", "cash"],
  ["/contacts", "contacts"],
  ["/acquisition", "acquisition"],
  ["/signing", "signing"],
  ["/inventory", "inventory"],
  ["/consignment", "consignment"],
  ["/purchasing", "purchasing"],
  ["/stocktake", "stocktake"],
  ["/campaigns", "campaigns"],
  ["/menu", "menu"],
  ["/reports", "reports"],
  ["/settings", "settings"],
  ["/backup", "backup"],
];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill('input[name="username"]', "dev-manager");
await page.fill('input[name="password"]', "dev-test-123456");
await page.click('button:has-text("登入")');
await page.waitForURL(`${BASE}/`);

const inventory = {};
for (const [route, slug] of ROUTES) {
  await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  const data = await page.evaluate(() => {
    const txt = (el) => (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ");
    const uniq = (a) => [...new Set(a.filter(Boolean))];
    return {
      title: document.title,
      headings: uniq([...document.querySelectorAll("h1,h2,h3")].map((e) => `${e.tagName}:${txt(e)}`)),
      buttons: uniq([...document.querySelectorAll("button")].map(txt)),
      links: uniq([...document.querySelectorAll("main a")].map(txt)),
      labels: uniq([...document.querySelectorAll("label")].map(txt)),
      inputs: uniq(
        [...document.querySelectorAll("input,select,textarea")].map((e) => {
          const id = e.getAttribute("id") || "";
          const lab = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
          return `${e.tagName.toLowerCase()}[${e.getAttribute("type") || ""}] name=${e.getAttribute("name") || ""} ph=${e.getAttribute("placeholder") || ""} label=${lab ? txt(lab) : ""}`;
        }),
      ),
      tableHeaders: [...document.querySelectorAll("table")].map((t) =>
        [...t.querySelectorAll("thead th")].map(txt).join(" | "),
      ),
      selectOptions: [...document.querySelectorAll("select")].map((s) => ({
        name: s.getAttribute("name") || s.getAttribute("id") || "",
        options: [...s.options].map((o) => o.text.trim()),
      })),
      bodyText: txt(document.querySelector("main") || document.body).slice(0, 4000),
    };
  });
  inventory[route] = data;
  await page.screenshot({ path: `${SHOTS}/${slug}.png`, fullPage: true });
  console.log(`✅ ${route}  buttons=${data.buttons.length} inputs=${data.inputs.length}`);
}

inventory.__errors = errors;
writeFileSync(`${SHOTS}/inventory.json`, JSON.stringify(inventory, null, 2));
console.log(`\n📄 ${SHOTS}/inventory.json  (JS errors: ${errors.length})`);
await browser.close();
