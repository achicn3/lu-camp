// 以 headless chromium 將截圖 PNG 轉為壓縮 WebP（Base64），輸出成 JSON 供手冊產生器內嵌。
// 無外部影像套件依賴。用法：node convert-images.mjs <manifest.json> <out.json>
import { readFileSync, writeFileSync } from "node:fs";

import { chromium } from "playwright";

const manifestPath = process.argv[2];
const outPath = process.argv[3];
const manifest = JSON.parse(readFileSync(manifestPath, "utf8")); // [{id, file, maxWidth?, quality?}]

const browser = await chromium.launch();
const page = await browser.newPage();
const out = {};
let total = 0;

for (const item of manifest) {
  const raw = readFileSync(item.file).toString("base64");
  const result = await page.evaluate(
    async ({ raw, maxWidth, quality }) => {
      const img = new Image();
      img.src = `data:image/png;base64,${raw}`;
      await img.decode();
      const scale = Math.min(1, maxWidth / img.naturalWidth);
      const w = Math.round(img.naturalWidth * scale);
      const h = Math.round(img.naturalHeight * scale);
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(img, 0, 0, w, h);
      return { data: canvas.toDataURL("image/webp", quality), w, h };
    },
    { raw, maxWidth: item.maxWidth ?? 1100, quality: item.quality ?? 0.72 },
  );
  out[item.id] = { src: result.data, w: result.w, h: result.h };
  total += result.data.length;
  console.log(`${item.id}: ${result.w}×${result.h}  ${(result.data.length / 1024).toFixed(0)} KB`);
}

writeFileSync(outPath, JSON.stringify(out));
console.log(`\n共 ${Object.keys(out).length} 張，Base64 合計 ${(total / 1024 / 1024).toFixed(2)} MB`);
await browser.close();
