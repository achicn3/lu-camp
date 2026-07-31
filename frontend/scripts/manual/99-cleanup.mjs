// 手冊 99：收尾——刪除保存的登入權杖檔。
// 本專案裁示「登入永不過期」，storageState 內的權杖等同長期有效的管理員鑰匙，
// 手冊產完（或中途放棄）都必須執行此腳本。可重複執行。
import { existsSync } from "node:fs";

import { purgeStateFiles, statePath } from "./_lib.mjs";

const targets = ["kiosk-state.json", "staff-state.json"].map(statePath);
purgeStateFiles();
const remaining = targets.filter((p) => existsSync(p));
if (remaining.length > 0) {
  console.error(`❌ 仍有權杖檔未刪除，請手動處理：\n${remaining.join("\n")}`);
  process.exitCode = 1;
} else {
  console.log("✅ 已刪除所有登入權杖檔：");
  for (const p of targets) console.log(`   ${p}`);
}
