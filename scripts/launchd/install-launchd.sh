#!/bin/bash
# 把 scripts/launchd/*.plist.template 套用這台機器的路徑後裝進 ~/Library/LaunchAgents/，
# 並 (re)bootstrap 三個服務＋每日備份排程（backend/frontend/hardware-agent/daily-backup；
# postgres 走 brew services，
# 不在這裡處理）。可重複執行（冪等）：改過 template 或 rebuild 過 frontend 之後直接重跑。
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"

mkdir -p "$HOME/Library/Logs/lucamp"

if [ "$(stat -f %Su "$LAUNCH_AGENTS" 2>/dev/null || echo "")" != "$(whoami)" ]; then
    echo "==> ~/Library/LaunchAgents 不是目前使用者所有，修正擁有權（需要密碼）"
    sudo chown "$(whoami)":staff "$LAUNCH_AGENTS"
fi

# daily-backup 是每日排程（非常駐），一併由這支安裝。
for svc in backend frontend hardware-agent daily-backup; do
    template="$REPO_DIR/scripts/launchd/com.lucamp.$svc.plist.template"
    target="$LAUNCH_AGENTS/com.lucamp.$svc.plist"
    sed -e "s|__REPO_DIR__|$REPO_DIR|g" -e "s|__HOME__|$HOME|g" "$template" > "$target"
    plutil -lint "$target" >/dev/null

    label="gui/$UID_N/com.lucamp.$svc"
    if launchctl print "$label" >/dev/null 2>&1; then
        launchctl bootout "$label" || true
        # bootout 是非同步的：緊接著 bootstrap 同一個 label 常會撞上「還沒真的卸載完」，
        # 出現 launchctl bootstrap 回「Input/output error」（實測踩過）。等它真的消失再繼續。
        for _ in $(seq 1 20); do
            launchctl print "$label" >/dev/null 2>&1 || break
            sleep 0.5
        done
    fi
    for attempt in 1 2 3; do
        if launchctl bootstrap "gui/$UID_N" "$target" 2>/tmp/lucamp-bootstrap-err; then
            break
        fi
        if [ "$attempt" -eq 3 ]; then
            cat /tmp/lucamp-bootstrap-err >&2
            exit 1
        fi
        sleep 1
    done
    echo "==> com.lucamp.$svc 已 (re)bootstrap"
done

echo "==> 完成。用 launchctl print gui/\$(id -u)/com.lucamp.backend 看狀態，"
echo "    或 tail -f ~/Library/Logs/lucamp/*.log 看 log。"
