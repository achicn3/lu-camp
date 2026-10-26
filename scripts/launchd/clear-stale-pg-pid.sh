#!/bin/bash
# launchd 用：開機時清掉 Postgres 過期的鎖定檔 postmaster.pid（2026-10-26 事故）。
#
# 事故：關機時 Postgres 沒關乾淨（被強制關機），postmaster.pid 留著舊的 PID。開機後那個
# PID 剛好被別的程式拿去用，Postgres 以為「另一個 postmaster 還活著」而拒絕啟動
# （log：lock file "postmaster.pid" already exists），整個系統連不上 DB；連重開 7 次，
# 直到某次 PID 沒撞到才起來。
#
# 做法：開機後輪詢一段時間。鎖定檔上的 PID 若不是一個活著的 postgres 行程，就把檔案刪掉；
# Postgres 本身由 brew services（KeepAlive）每 10 秒重試，下一輪就能正常啟動並自動修復。
# 刻意的設計，改動前請先理解：
# - **只在 PID 不是 postgres 時才刪**：PID 是活著的 postgres 代表 DB 正在跑，刪了會出大事。
# - **連續兩次看到同一份過期內容才刪**：Postgres 剛啟動時會先建空檔再寫入 PID，
#   只看一次可能剛好撞上那一瞬間，把正在啟動的 DB 的鎖定檔刪掉。
# - 看到活著的 postgres 就結束；時間到也結束（不常駐）。
# macOS 內建 bash 是 3.2，這支只用 3.2 有的語法。
set -euo pipefail
PGDATA="${PGDATA:-/opt/homebrew/var/postgresql@16}"
PID_FILE="$PGDATA/postmaster.pid"
WAIT_SECONDS="${WAIT_SECONDS:-300}"
INTERVAL="${INTERVAL:-5}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# 鎖定檔上的 PID 是不是一個活著的 postgres 行程
pid_is_live_postgres() {
    local comm
    [ -n "$1" ] || return 1
    comm="$(ps -p "$1" -o comm= 2>/dev/null || true)"
    [ "$(basename "${comm:-none}")" = "postgres" ]
}

seen_stale=0
previous_stale=""
deadline=$(( $(date +%s) + WAIT_SECONDS ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    if [ -f "$PID_FILE" ]; then
        content="$(cat "$PID_FILE" 2>/dev/null || true)"
        pid="$(printf '%s\n' "$content" | head -n1 | tr -dc '0-9')"
        if pid_is_live_postgres "$pid"; then
            log "postgres 正在執行（PID ${pid}），不需處理"
            exit 0
        fi
        # 用 seen_stale 旗標而不是看 previous_stale 是否非空：空檔的內容本來就是空字串。
        if [ "$seen_stale" -eq 1 ] && [ "$content" = "$previous_stale" ]; then
            rm -f "$PID_FILE"
            log "已清掉過期的 ${PID_FILE}（PID ${pid:-空} 不是 postgres），等 brew services 重新啟動 postgres"
            seen_stale=0
        else
            seen_stale=1
            previous_stale="$content"
        fi
    else
        seen_stale=0
    fi
    sleep "$INTERVAL"
done
log "等待 ${WAIT_SECONDS} 秒結束"
