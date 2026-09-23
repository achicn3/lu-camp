#!/bin/bash
# 每日資料庫備份（本機 Homebrew postgresql@16；暫代失效的 app 內建備份排程）。
#
# 為什麼有這支：app 的備份模組走 `docker exec lu-camp-db-1 pg_dump`（docs/28），
# 但這台正式機沒有 docker、資料庫是 Homebrew 原生的，排程從來沒成功過
# （backend.error.log 一直在噴 FileNotFoundError: 'docker'）。修好 app 之前先用這支頂著。
#
# ⚠️ 這是「同一台機器上的本機備份」——防得了誤刪與升級失敗，**防不了整機滅失／失竊／火災**。
#    異地加密備份（R2）仍要靠修好 app 的備份模組。
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PG_BIN=/opt/homebrew/opt/postgresql@16/bin
KEEP=7                                   # 保留最新 7 份，其餘刪除

# 憑證從 repo 根 .env 讀，不寫死在腳本裡。set -a 讓變數 export 給 pg_dump 的子程序。
set -a
# shellcheck disable=SC1091
. "$REPO_DIR/.env"
set +a

DB="${POSTGRES_DB:?POSTGRES_DB 未設}"
PGUSER_="${POSTGRES_USER:?POSTGRES_USER 未設}"
export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD 未設}"
DIR="${BACKUP_LOCAL_DIR:-$HOME/lu-camp-backups}"

mkdir -p "$DIR"
chmod 700 "$DIR"                         # 目錄含個資明文（姓名/電話），限本使用者

STAMP="$(date +%Y%m%d)"
OUT="$DIR/${DB}_${STAMP}.dump"
TMP="$OUT.partial"                       # 先寫暫存檔：中途失敗不會留下半截檔冒充當日備份

cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

echo "[$(date '+%F %T')] 開始備份 $DB → $OUT"

# 1) dump（custom format，含 BYTEA 簽名影像）
"$PG_BIN/pg_dump" -h 127.0.0.1 -U "$PGUSER_" -Fc -d "$DB" -f "$TMP"

# 2) 驗 dump 可讀且非空——空檔/壞檔在此擋下，絕不把失敗記成功
test -s "$TMP"
"$PG_BIN/pg_restore" --list "$TMP" > /dev/null

# 3) 驗過才就位（同目錄 mv 是原子操作）
chmod 600 "$TMP"
mv -f "$TMP" "$OUT"
trap - EXIT
echo "[$(date '+%F %T')] 備份完成：$(du -h "$OUT" | cut -f1)"

# 4) 修剪：**只在今天這份成功且驗過之後才刪舊的**，否則備份壞掉還會把好的一起清光。
#    只挑每日檔名樣式 <db>_YYYYMMDD.dump，手動／升級前的備份（例如 *_preupgrade_*）不會被動到。
#    （macOS 內建的是 bash 3.2，沒有 mapfile，故用 while read）
find "$DIR" -maxdepth 1 -type f -name "${DB}_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].dump" \
  | sort -r | tail -n +$((KEEP + 1)) \
  | while IFS= read -r f; do
      rm -f -- "$f"
      echo "[$(date '+%F %T')] 刪除逾期備份：$(basename "$f")"
    done

echo "[$(date '+%F %T')] 目前保留 $(find "$DIR" -maxdepth 1 -name "${DB}_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].dump" | wc -l | tr -d ' ') 份每日備份"
