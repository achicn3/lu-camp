#!/bin/bash
# IP 換了、或改了 frontend/.env.local 之後要跑這支：NEXT_PUBLIC_* 是 build 當下
# 寫死進 JS 的，不重新 build 不會生效。跑完會自動重啟 launchd 管的 frontend 服務。
#
# 位址一律交給 Next 自己讀 frontend/.env.local：Next 建置時「shell 環境變數」優先於 .env.local，
# 升級步驟前面 `source ../.env` 過的話，那份檔案裡的舊位址會被寫進 JS、整台店連不到後端
# （2026-10-04 實際發生）。所以先把 shell 裡的這兩個變數拿掉再 build。
#
# Next build 一開始就會清掉 .next（只留 cache/dev/lock），所以先備份目前的前端：
# build 失敗、或建出來的位址不對，就還原成備份、不重啟——正在跑的前端才真的照常可用。
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR/frontend"

DIST=".next"
BACKUP=".next.prev"
BACKUP_TMP=".next.prev.partial"
LOG="$(mktemp)"
# 只排除最上層的 dev/cache/lock（Next 清 .next 時保留的那三個），不誤傷名為 dev 的路由。
SYNC_EXCLUDES=(--exclude /dev --exclude /cache --exclude /lock)
BACKUP_OK=0
RESTORE_MSG=""

# 上次還原途中失敗留下的備份，可能是唯一完好的一份：不要刪掉它，先人工確認。
if [[ -e "$BACKUP" ]]; then
  echo "中止：frontend/$BACKUP 還在（上次重建沒有正常結束），請先確認前端狀態再處理這個備份" >&2
  exit 1
fi

restore() {
  # 只有備份完整（先抄到暫存、成功才改名）才還原；否則 .next 根本還沒被動過，不能拿殘缺的備份蓋回去。
  if [[ "$BACKUP_OK" == 1 && -d "$BACKUP" ]]; then
    if rsync -a --delete "${SYNC_EXCLUDES[@]}" "$BACKUP/" "$DIST/"; then
      rm -rf "$BACKUP"
      RESTORE_MSG="已還原成上一版前端，未重啟 frontend（目前的前端照常可用）"
    else
      RESTORE_MSG="還原失敗：上一版前端備份留在 frontend/$BACKUP，請勿重啟 frontend，把這段訊息給店主看"
    fi
  fi
}
fail() {
  # 先還原、最後才印訊息：遠端斷線時 stderr 可能已經寫不進去（寫入失敗或 SIGPIPE 會讓 shell
  # 在還原之前就結束，留下半套 .next）。這裡關掉 errexit、忽略 SIGPIPE，確保還原一定跑完。
  trap - ERR INT TERM HUP
  trap '' PIPE
  set +e
  restore
  rm -rf "$BACKUP_TMP" "$LOG"
  echo "中止：$1" >&2
  [[ -n "$RESTORE_MSG" ]] && echo "$RESTORE_MSG" >&2
  exit 1
}
trap 'fail "執行失敗（見上方輸出）"' ERR
# build 要跑好幾分鐘且一開始就清掉 .next：Ctrl-C、遠端斷線也要還原，不能留半套。
trap 'fail "被中斷"' INT TERM HUP

rm -rf "$BACKUP_TMP"
if [[ -d "$DIST" ]]; then
  rsync -a "${SYNC_EXCLUDES[@]}" "$DIST/" "$BACKUP_TMP/"
  mv "$BACKUP_TMP" "$BACKUP"
  BACKUP_OK=1
fi

env -u NEXT_PUBLIC_API_BASE_URL -u NEXT_PUBLIC_AGENT_URL /opt/homebrew/bin/pnpm run build 2>&1 | tee "$LOG"
trap - ERR

# 驗 Next 實際用的位址（check-build-env.mjs 印出的「建置位址」）確實寫進了 JS。
API_URL="$(sed -n 's/^  NEXT_PUBLIC_API_BASE_URL=//p' "$LOG" | tail -1)"
AGENT_URL="$(sed -n 's/^  NEXT_PUBLIC_AGENT_URL=//p' "$LOG" | tail -1)"
URL_RE='^https?://[^[:space:]#]+$'
[[ "${API_URL}" =~ $URL_RE && "${AGENT_URL}" =~ $URL_RE ]] \
  || fail "讀不到建置位址，或位址格式不對（API='${API_URL}'、AGENT='${AGENT_URL}'）"
grep -rqF -- "${API_URL}" "$DIST/static/chunks" && grep -rqF -- "${AGENT_URL}" "$DIST/static/chunks" \
  || fail "建出來的前端沒有寫入 ${API_URL}／${AGENT_URL}"

rm -rf "$BACKUP" "$LOG"
launchctl kickstart -k "gui/$(id -u)/com.lucamp.frontend"
echo "frontend 已重新 build 並重啟（後端 ${API_URL}、硬體代理 ${AGENT_URL}）"
