// 菜單照片瀏覽器煙霧（docs/44 §3.4；O1d）：
// 菜單頁上傳一張帶 GPS 的大 JPEG → 縮圖出現 → 公開網址（不帶登入）拿得到 WebP、長邊 1200、沒有 EXIF
// → 上傳 iPhone 的 HEIC 換照片 → POS 磚顯示照片 → 上傳 PDF 被擋並顯示原因 → 移除照片。
//
// 測試圖檔由 backend 的 Pillow 現場產生（帶 GPS 的 JPEG、HEIC），不放二進位檔進 repo。
// 需 backend + frontend 已起，且指向隔離測試庫（SMOKE_ALLOW_WRITE=1）；
// SMOKE_BACKEND_DIR 指向 backend 資料夾（產生測試圖用）。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
const BACKEND_DIR = process.env.SMOKE_BACKEND_DIR ?? resolve(process.cwd(), "..", "backend");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立品項與照片，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const run = randomUUID().slice(0, 6);
const cakeName = `戚風-${run}`;
const work = join(SHOTS, `photo-fixtures-${run}`);
mkdirSync(work, { recursive: true });
const jpegPath = join(work, "cake.jpg");
const heicPath = join(work, "cake.heic");
const pdfPath = join(work, "menu.pdf");
writeFileSync(pdfPath, "%PDF-1.4 not a photo");
execFileSync(
  "uv",
  [
    "run",
    "python",
    "-c",
    `
import pillow_heif
from PIL import Image, ImageDraw
pillow_heif.register_heif_opener()
img = Image.new("RGB", (3000, 2000), (226, 196, 150))
d = ImageDraw.Draw(img)
d.ellipse((700, 300, 2300, 1700), fill=(244, 222, 170), outline=(150, 100, 60), width=40)
exif = Image.Exif()
exif.get_ifd(0x8825)[2] = (25.0, 2.0, 0.0)
img.save(${JSON.stringify(jpegPath)}, format="JPEG", exif=exif.tobytes())
Image.new("RGB", (1500, 2000), (120, 160, 90)).save(${JSON.stringify(heicPath)}, format="HEIF")
`,
  ],
  { cwd: BACKEND_DIR, stdio: "inherit" },
);

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

let token = "";
async function api(method, path, body) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
}
async function itemNow(id) {
  return (await api("GET", "/api/v1/menu-items")).body.find((i) => i.id === id);
}

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));

  const created = await api("POST", "/api/v1/menu-items", {
    name: cakeName,
    unit_price: "90",
    category: `甜點-${run}`,
  });
  ok("建立品項", created.status === 201, `HTTP ${created.status}`);
  const cakeId = created.body.id;

  // 1. 上傳帶 GPS 的 JPEG
  await page.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  const row = page.locator("tr", { hasText: cakeName });
  await row.getByLabel(`${cakeName} 上傳照片`).setInputFiles(jpegPath);
  const thumb = row.getByRole("img", { name: `${cakeName} 照片` });
  await thumb.waitFor({ timeout: 20000 });
  await page.waitForFunction((el) => el.complete && el.naturalWidth > 0, await thumb.elementHandle());
  const first = (await itemNow(cakeId)).photo_sha256;
  ok("上傳後品項有照片", typeof first === "string" && first.length === 64, String(first));
  await row.screenshot({ path: `${SHOTS}/photo-01-menu-row.png` });

  // 2. 公開網址不帶登入也拿得到；WebP、長邊 1200、沒有 GPS
  const pub = await fetch(`${API}/api/v1/menu-photos/${first}.webp`);
  const bytes = Buffer.from(await pub.arrayBuffer());
  ok("公開網址不需登入、是 WebP", pub.status === 200 && pub.headers.get("content-type") === "image/webp");
  const meta = execFileSync(
    "uv",
    [
      "run",
      "python",
      "-c",
      "import io,sys,json;from PIL import Image;im=Image.open(io.BytesIO(sys.stdin.buffer.read()));" +
        "print(json.dumps({'size':im.size,'exif':len(im.getexif()),'fmt':im.format}))",
    ],
    { cwd: BACKEND_DIR, input: bytes },
  ).toString();
  const info = JSON.parse(meta);
  ok(
    "長邊縮到 1200、去掉 EXIF（含 GPS）",
    info.fmt === "WEBP" && info.size[0] === 1200 && info.size[1] === 800 && info.exif === 0,
    meta.trim(),
  );

  // 3. 換成 iPhone 的 HEIC
  await row.getByLabel(`${cakeName} 上傳照片`).setInputFiles(heicPath);
  let second = first;
  for (let i = 0; i < 40 && second === first; i++) {
    await page.waitForTimeout(250);
    second = (await itemNow(cakeId)).photo_sha256;
  }
  ok("HEIC 可上傳、換成新照片", typeof second === "string" && second !== first, String(second));
  await row.getByRole("img", { name: `${cakeName} 照片` }).and(page.locator(`img[src*="${second}"]`)).waitFor();

  // 4. POS 磚顯示照片
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  const tab = page.getByRole("tab", { name: `甜點-${run}` });
  if ((await tab.count()) > 0) await tab.click();
  const tile = page.locator(".pos-menu-tile", { hasText: cakeName });
  const tileImg = tile.locator("img");
  await tileImg.waitFor();
  await page.waitForFunction((el) => el.complete && el.naturalWidth > 0, await tileImg.elementHandle());
  ok("POS 磚顯示照片", (await tileImg.getAttribute("src")).includes(second));
  await page.locator(".pos-menu").screenshot({ path: `${SHOTS}/photo-02-pos-tile.png` });

  // 5. 上傳 PDF：被擋、顯示原因、照片不變
  await page.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
  const row2 = page.locator("tr", { hasText: cakeName });
  await row2.getByLabel(`${cakeName} 上傳照片`).setInputFiles(pdfPath);
  const alert = row2.getByRole("alert");
  await alert.waitFor();
  ok("PDF 被擋並說明可接受的格式", (await alert.textContent()).includes("JPEG"), await alert.textContent());
  ok("被擋後照片不變", (await itemNow(cakeId)).photo_sha256 === second);
  await row2.screenshot({ path: `${SHOTS}/photo-03-rejected.png` });

  // 6. 移除照片
  await row2.getByRole("button", { name: `${cakeName} 移除照片` }).click();
  await row2.getByText("上傳照片").waitFor();
  ok("移除後品項沒有照片", (await itemNow(cakeId)).photo_sha256 === null);
} catch (err) {
  ok("流程執行", false, String(err));
  await page.screenshot({ path: `${SHOTS}/photo-error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
