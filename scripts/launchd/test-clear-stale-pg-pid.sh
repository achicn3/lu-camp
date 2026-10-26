#!/bin/bash
# clear-stale-pg-pid.sh 的測試：用暫存目錄當 PGDATA，不碰真正的資料庫。
# 跑法：scripts/launchd/test-clear-stale-pg-pid.sh（全過 exit 0，任一失敗 exit 1）
set -euo pipefail
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/clear-stale-pg-pid.sh"
TMP="$(mktemp -d)"
FAILED=0
trap 'rm -rf "$TMP"' EXIT

run() {  # 短等待、快輪詢，讓測試幾秒內跑完
    PGDATA="$TMP" WAIT_SECONDS="$1" INTERVAL=0.2 bash "$SCRIPT" >/dev/null
}

check() {  # check <說明> <預期 present|absent>
    local actual=absent
    [ -f "$TMP/postmaster.pid" ] && actual=present
    if [ "$actual" = "$2" ]; then
        echo "PASS  $1"
    else
        echo "FAIL  $1（預期 $2，實際 ${actual}）"
        FAILED=1
    fi
}

write_pid_file() {
    printf '%s\n%s\n1792988892\n5432\n/tmp\nlocalhost\n  913914     65536\nready   \n' "$1" "$TMP" \
        > "$TMP/postmaster.pid"
}

# 1. 事故重現：鎖定檔上的編號被別的程式（不是 postgres）佔走 → 要刪
sleep 30 &
OTHER_PID=$!
write_pid_file "$OTHER_PID"
run 2
check "編號被非 postgres 程式佔用 → 清掉" absent
kill "$OTHER_PID" 2>/dev/null || true
wait "$OTHER_PID" 2>/dev/null || true

# 2. 編號已經沒有任何程式在用 → 要刪
write_pid_file 999999
run 2
check "編號不存在 → 清掉" absent

# 3. 內容是空的（壞檔）→ 要刪
: > "$TMP/postmaster.pid"
run 2
check "空檔 → 清掉" absent

# 4. 編號是活著的 postgres → 絕對不能刪
PG_PID="$(pgrep -x postgres | head -n1 || true)"
if [ -n "$PG_PID" ]; then
    write_pid_file "$PG_PID"
    run 2
    check "編號是活著的 postgres → 保留" present
    rm -f "$TMP/postmaster.pid"
else
    echo "SKIP  編號是活著的 postgres（這台沒在跑 postgres）"
fi

# 5. 沒有鎖定檔 → 什麼都不做、時間到正常結束
run 1
check "沒有鎖定檔 → 不產生任何東西" absent

exit "$FAILED"
