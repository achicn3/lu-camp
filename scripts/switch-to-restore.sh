#!/usr/bin/env bash
# 受控切換腳本（docs/31 §6）：把「已四驗通過（VERIFIED）的還原庫」正式切為營運庫。
#
# 為什麼要腳本、不由 App 自動做：Postgres 不允許改名/刪除「有連線」的資料庫，App 自己連著
# 正式庫時無法把自己底下的庫換掉；單機中途失敗會兩頭落空。故切換＝停機、由店家手動、可回退。
#
# 安全設計：
#   1) 只接受一個已存在的還原庫名（lucamp_restore_<時戳>），且應先在 /backup 儀表板確認其為「四驗通過」。
#   2) 用「改名」而非「刪除」：live -> lucamp_old_<時戳>（保留可回退），restore -> live。
#   3) 切換前務必已停後端（本腳本會踢掉殘餘連線，但仍請先停 App 以免它立刻重連）。
#
# 用法：
#   # 先停後端（uvicorn / docker compose stop backend），再：
#   BACKUP_DOCKER_BIN="/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe" \
#     scripts/switch-to-restore.sh lucamp_restore_20260719_044909
#   # 切換後重啟後端；確認營運正常且數日無誤後，再手動刪除 lucamp_old_<時戳>。
#
# 回退：ALTER DATABASE "lucamp" RENAME TO "lucamp_restore_bad"; ALTER DATABASE "lucamp_old_<時戳>" RENAME TO "lucamp";
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "用法：$0 <lucamp_restore_YYYYMMDD_HHMMSS>" >&2
  exit 2
fi
RESTORE_DB="$1"
LIVE_DB="${LIVE_DB:-lucamp}"
DOCKER="${BACKUP_DOCKER_BIN:-docker}"
CONTAINER="${BACKUP_DB_CONTAINER:-lu-camp-db-1}"
DB_USER="${POSTGRES_USER:-lucamp}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OLD_DB="${LIVE_DB}_old_${STAMP}"

# 還原庫名嚴格樣式（只接受 restore 演練產生的名字；擋 postgres/live 等任意庫名）
# 名字含短 UUID 後綴（防同秒碰撞，Codex 第三輪 #2）。
if ! [[ "$RESTORE_DB" =~ ^lucamp_restore_[0-9]{8}_[0-9]{6}_[0-9a-f]{8}$ ]]; then
  echo "錯誤：只接受 lucamp_restore_YYYYMMDD_HHMMSS_<uuid8> 樣式的還原庫名：$RESTORE_DB" >&2
  exit 2
fi
if [[ "$RESTORE_DB" == "$LIVE_DB" || "$LIVE_DB" == "postgres" ]]; then
  echo "錯誤：LIVE_DB 設定不合法（$LIVE_DB）。" >&2
  exit 2
fi

psql_postgres() { "$DOCKER" exec "$CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 "$@"; }
db_exists() { psql_postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$1'" | grep -q 1; }

# 前置檢查：還原庫存在、營運庫存在
if ! db_exists "$RESTORE_DB"; then
  echo "錯誤：還原庫不存在：$RESTORE_DB（請先從 /backup 觸發還原並確認四驗通過）" >&2
  exit 1
fi
if ! db_exists "$LIVE_DB"; then
  echo "錯誤：營運庫不存在：$LIVE_DB" >&2
  exit 1
fi

# 前置檢查：restore_runs 必須有此還原庫「四驗通過（VERIFIED）」的紀錄（切換前查,查營運庫）
verified=$("$DOCKER" exec "$CONTAINER" psql -U "$DB_USER" -d "$LIVE_DB" -tAc \
  "SELECT count(*) FROM restore_runs WHERE restore_db_name='$RESTORE_DB' AND status='VERIFIED'" 2>/dev/null || echo 0)
if [[ "${verified:-0}" -lt 1 ]]; then
  echo "錯誤：$RESTORE_DB 在 restore_runs 沒有『VERIFIED』紀錄——拒絕切換未驗證的還原庫。" >&2
  exit 1
fi

echo "== 切換前確認 =="
echo "  營運庫 LIVE_DB   = $LIVE_DB  → 將改名保留為 $OLD_DB"
echo "  還原庫 RESTORE_DB = $RESTORE_DB  → 將改名為 $LIVE_DB（restore_runs 已確認 VERIFIED）"
echo "請確認：①後端已停 ②清楚這會停機切換營運庫。"
read -r -p "輸入 SWITCH 以繼續：" ans
if [[ "$ans" != "SWITCH" ]]; then
  echo "已取消。"
  exit 0
fi

# 踢掉兩庫殘餘連線（後端沒停乾淨時的保險）
psql_postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$LIVE_DB','$RESTORE_DB') AND pid <> pg_backend_pid();" >/dev/null

# 步驟一：live → old。失敗即中止,營運庫原封不動。
if ! psql_postgres -c "ALTER DATABASE \"$LIVE_DB\" RENAME TO \"$OLD_DB\";"; then
  echo "錯誤：改名 $LIVE_DB → $OLD_DB 失敗（可能仍有連線）。營運庫未動,請重試。" >&2
  exit 1
fi

# 步驟二：restore → live。失敗即回滾（old → live 改回）,確保營運庫不會兩頭落空（Codex #3）。
if ! psql_postgres -c "ALTER DATABASE \"$RESTORE_DB\" RENAME TO \"$LIVE_DB\";"; then
  echo "錯誤：改名 $RESTORE_DB → $LIVE_DB 失敗,回滾中…" >&2
  if psql_postgres -c "ALTER DATABASE \"$OLD_DB\" RENAME TO \"$LIVE_DB\";"; then
    echo "已回滾：$OLD_DB → $LIVE_DB，營運庫恢復原狀。請排除 $RESTORE_DB 的連線後重試。" >&2
  else
    echo "嚴重：回滾也失敗！營運庫目前名為 $OLD_DB，請手動執行：" >&2
    echo "  \"$DOCKER\" exec $CONTAINER psql -U $DB_USER -d postgres -c 'ALTER DATABASE \"$OLD_DB\" RENAME TO \"$LIVE_DB\"'" >&2
  fi
  exit 1
fi

# 確認最終狀態：live 存在、old 存在
if ! db_exists "$LIVE_DB" || ! db_exists "$OLD_DB"; then
  echo "警告：切換後狀態異常，請手動檢查 pg_database。" >&2
  exit 1
fi

echo ""
echo "✅ 切換完成：$RESTORE_DB 現為營運庫 $LIVE_DB；原營運庫保留為 $OLD_DB（可回退）。"
echo "下一步：重啟後端（uvicorn / docker compose start backend）→ 驗證營運正常。"
echo "確認數日無誤後再刪舊庫：\"$DOCKER\" exec $CONTAINER psql -U $DB_USER -d postgres -c 'DROP DATABASE \"$OLD_DB\"'"
