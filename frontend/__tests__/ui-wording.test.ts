// 畫面用詞守衛：擋掉程式常數外洩、工程術語、公文腔、疊字。
//
// **為什麼要這支**：本專案反覆出現「畫面上跑出 REPORT_ONLY」「要店員自己打英文代碼」
// 「請請店長登入」這類問題，而且每次都是**人工看截圖**才發現的。判斷「一般人看不看得懂」
// 沒辦法完全自動化，但上面那幾類是規則抓得到的，抓得到的就不該再靠肉眼。
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const ROOTS = ["app", "features"];

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...sources(full));
    else if (full.endsWith(".tsx") || full.endsWith(".ts")) out.push(full);
  }
  return out;
}

/** 取出「使用者看得到」的中文字串：字面值與 JSX 文字節點，跳過註解。 */
function visibleText(file: string): { line: number; text: string }[] {
  const out: { line: number; text: string }[] = [];
  file.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (line.startsWith("//") || line.startsWith("*") || line.startsWith("/*")) return;
    const re = /"([^"\\]{2,200})"|'([^'\\]{2,200})'|>([^<>{}\n]{2,200})</g;
    for (let m = re.exec(raw); m !== null; m = re.exec(raw)) {
      const text = (m[1] ?? m[2] ?? m[3] ?? "").trim();
      if (/[\u4e00-\u9fff]/.test(text)) out.push({ line: i + 1, text });
    }
  });
  return out;
}

const FILES = ROOTS.flatMap((r) => sources(r)).map((path) => ({
  path,
  entries: visibleText(readFileSync(path, "utf8")),
}));

function findAll(predicate: (text: string) => string | null): string[] {
  const hits: string[] = [];
  for (const { path, entries } of FILES) {
    for (const { line, text } of entries) {
      const why = predicate(text);
      if (why !== null) hits.push(`${path}:${line} 「${text.slice(0, 60)}」 ← ${why}`);
    }
  }
  return hits;
}

describe("畫面用詞", () => {
  it("不把程式常數丟到畫面上", () => {
    // 允許的通用字：店主裁示 POS 算通用字；品牌與檔案格式亦然。
    const OK = new Set(["POS", "LINE", "PAY", "QR", "CSV", "PDF", "HTML", "MB", "KB", "GB"]);
    expect(
      findAll((t) => {
        const m = t.match(/\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b|[「（(\s:：]([A-Z]{4,})[」）)\s,，。]/);
        if (m === null) return null;
        const token = (m[1] ?? m[0]).trim();
        return OK.has(token) ? null : token;
      }),
    ).toEqual([]);
  });

  it("不出現工程術語", () => {
    const JARGON = ["字串", "布林", "陣列", "雜湊", "序列化", "冪等", "佇列", "拋檔",
      "落庫", "端點", "快取", "非同步", "執行緒", "口令", "受控腳本", "驗證庫"];
    expect(findAll((t) => JARGON.find((j) => t.includes(j)) ?? null)).toEqual([]);
  });

  it("不出現公文腔", () => {
    const STIFF = ["此操作", "俾便", "業已", "應予", "係為", "逕行", "予以", "務請",
      "之虞", "所涉", "殊難", "留痕", "裁定", "清場"];
    expect(findAll((t) => STIFF.find((w) => t.includes(w)) ?? null)).toEqual([]);
  });

  it("沒有疊字", () => {
    // 「請請」「的的」這類手滑；中文疊詞（例如「剛剛」「常常」）不在此列，故只列黑名單。
    const DOUBLED = ["請請", "的的", "了了", "是是", "在在", "會會", "可可", "要要"];
    expect(findAll((t) => DOUBLED.find((d) => t.includes(d)) ?? null)).toEqual([]);
  });
});
