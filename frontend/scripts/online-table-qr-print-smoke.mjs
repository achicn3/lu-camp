// 桌號 QR 一鍵列印（店主 2026-10-15）真瀏覽器煙霧：菜單頁「列印桌號 QR」→ 列印頁每桌一張卡
// → 畫面上的 QR 解得回該桌網址、用手機打開就是那一桌的點餐頁 → 列印版面 A4 一頁六張、工具列不印。
// 單桌「列印 QR」只先勾那一桌。唯讀：不改設定、不送單。
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import jsQR from "jsqr";
import { chromium } from "playwright";
import { PNG } from "pngjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const API = "http://localhost:8116/api/v1", POS = "http://localhost:3116";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-table-qr-print-shots";
mkdirSync(SHOTS, { recursive: true });

const login = await (await fetch(`${API}/auth/login`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: "dev-manager", password: PASSWORD }),
})).json();
const status = await (await fetch(`${API}/online-order/status`, {
  headers: { Authorization: `Bearer ${login.access_token}` },
})).json();
assert.ok(status.configured && status.tables.length >= 2, "demo needs a configured cloud with tables");
const dineIn = status.tables.find((t) => t.service_mode === "DINE_IN");

const browser = await chromium.launch();
try {
  const pos = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  await skipOpeningCheckRedirect(pos);
  await pos.goto(`${POS}/login`);
  await pos.locator('input[name="username"]').fill("dev-manager");
  await pos.locator('input[name="password"]').fill(PASSWORD);
  await pos.getByRole("button", { name: "登入", exact: true }).click();
  await pos.waitForURL((url) => !url.pathname.startsWith("/login"));

  // ① 菜單頁入口 → 列印頁：每桌一張卡、預設全選
  await pos.goto(`${POS}/menu?section=online`);
  await pos.getByRole("link", { name: "列印桌號 QR" }).click();
  await pos.waitForURL(/\/menu\/table-qr$/);
  await pos.getByRole("button", { name: `列印 ${status.tables.length} 張` }).waitFor();
  assert.equal(await pos.getByRole("article").count(), status.tables.length);
  await pos.screenshot({ path: `${SHOTS}/01-print-page.png` });

  // ② 畫面上的 QR 解得回這桌的網址（真的從像素解，不是讀 DOM）
  const card = pos.getByRole("article", { name: `桌號 ${dineIn.label} 點餐 QR` });
  const png = PNG.sync.read(await card.getByRole("img").screenshot());
  const decoded = jsQR(new Uint8ClampedArray(png.data), png.width, png.height)?.data;
  assert.equal(decoded, dineIn.url, "QR pixels decode to the table's ordering URL");

  // ③ 用解出來的網址開客人頁：是這一桌的點餐頁
  const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await guest.goto(decoded.replace(/^https?:\/\/[^/]+/, "http://localhost:8794"));
  await guest.getByText(dineIn.label).first().waitFor();
  await guest.screenshot({ path: `${SHOTS}/02-guest-from-qr.png` });

  // ④ 列印版面：工具列與導覽不印；A4 一頁六張
  await pos.emulateMedia({ media: "print" });
  assert.ok(await pos.locator(".table-qr-toolbar").isHidden(), "toolbar is not printed");
  assert.ok(await pos.locator(".app-header").isHidden(), "app header is not printed");
  await pos.setViewportSize({ width: 794, height: 1123 }); // A4 @96dpi，看一頁的樣子
  await pos.getByRole("article").first().waitFor();
  await pos.screenshot({ path: `${SHOTS}/03-print-media.png` });
  const pdf = await pos.pdf({ preferCSSPageSize: true });
  writeFileSync(`${SHOTS}/04-print.pdf`, pdf);
  const pages = (pdf.toString("latin1").match(/\/Type\s*\/Page[^s]/g) ?? []).length;
  assert.equal(pages, Math.ceil(status.tables.length / 6), `PDF pages for ${status.tables.length} cards`);
  await pos.emulateMedia({ media: "screen" });
  await pos.setViewportSize({ width: 1280, height: 1000 });

  // ⑤ 單桌列印：只先勾那一桌
  await pos.goto(`${POS}/menu?section=online`);
  await pos.locator(".online-publish-tables > summary").click();
  await pos.getByRole("link", { name: `${dineIn.label} 列印 QR` }).click();
  await pos.getByRole("button", { name: "列印 1 張" }).waitFor();
  assert.equal(await pos.getByRole("article").count(), 1);
  await pos.screenshot({ path: `${SHOTS}/05-single-table.png` });
  console.log(`PASS menu → print page (${status.tables.length} cards) → QR pixels decode to ${dineIn.label} URL → guest page opens → print hides toolbar, ${pages} A4 page(s) → single-table link`);
} finally {
  await browser.close();
}
