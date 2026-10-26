#!/bin/bash
# 擋「變數後面直接接中文／全形標點」：`$BACKUP，` 要寫成 `${BACKUP}，`。
#
# macOS 內建 bash 3.2 在 UTF-8 語系（店主終端機是 zh_TW.UTF-8）會把緊接的多位元組字元
# 當成變數名稱的一部分，`set -u` 下直接 `unbound variable` 結束——偏偏都出在失敗／回滾路徑的
# 提示訊息，最需要看到訊息時腳本先死了（2026-10-26 查到 rebuild-frontend.sh、switch-to-restore.sh、
# bootstrap.sh）。launchd 不帶語系所以不發作，只有手動在終端機跑才會中。
# 跑法：scripts/check-shell-var-braces.sh（沒問題 exit 0；有問題列出位置並 exit 1）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
git ls-files -z '*.sh' | xargs -0 python3 -c '
import re, sys
pattern = re.compile(r"(?<!\\)\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])")
bad = 0
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as f:
        for no, line in enumerate(f, 1):
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                print(f"{path}:{no}: {line.rstrip()}")
                bad = 1
if bad:
    print("變數後面緊接非 ASCII 字元：請改成 ${VAR} 寫法", file=sys.stderr)
sys.exit(bad)
'
