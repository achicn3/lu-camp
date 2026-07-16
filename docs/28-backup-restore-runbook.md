# 28 — 備份／還原 Runbook（Postgres 主儲存＋R2 加密異地備份）

> 依 2026-07-16 裁示（docs/27）：簽名等全部資料以 **Postgres 為主儲存**；異地備份為
> **整庫 pg_dump 加密後上傳 Cloudflare R2**。本文件為實際演練驗證過的操作手冊
> （演練紀錄見文末）。
>
> **成本紀律（店主指示）**：R2 上傳次數嚴格節制。日常建議**每日一次**（免費額度綽綽有餘：
> 每日 ~3MB、Class A 寫入 1 次），並僅保留最近 30 份（舊檔刪除不收費）。
> **本 runbook 未設定自動排程**——待店主確認頻率後再開。

## 0. 憑證

全部在 `<repo>/.env.r2`（受 `.gitignore` 保護，**嚴禁 commit**）：
`R2_ENDPOINT`／`R2_ACCESS_KEY_ID`／`R2_SECRET_ACCESS_KEY`／`R2_BUCKET=pos`／
`R2_BACKUP_PASSPHRASE`（AES 加密口令）。
⚠️ **口令遺失＝備份全部作廢**。請將 `.env.r2` 另抄一份放店外實體保管（例如店主手機備忘錄）。

## 1. 備份（dump → 加密 → 上傳）

```bash
DOCKER="/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"
source /home/test/lu-camp/.env.r2
STAMP=$(date +%Y%m%d)
DB=lucamp            # 正式庫名；演練用 lucamp_sim

# 1) 容器內 dump（custom format，含 BYTEA 簽名影像）
"$DOCKER" exec lu-camp-db-1 pg_dump -U lucamp -Fc -d "$DB" -f /tmp/backup.dump
"$DOCKER" exec lu-camp-db-1 cat /tmp/backup.dump > /home/test/lu-camp-backups/${DB}_${STAMP}.dump

# 2) 加密（AES-256-CBC + PBKDF2 20 萬次）
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
  -in  /home/test/lu-camp-backups/${DB}_${STAMP}.dump \
  -out /home/test/lu-camp-backups/${DB}_${STAMP}.dump.enc \
  -pass "pass:$R2_BACKUP_PASSPHRASE"

# 3) 上傳 R2（boto3；bucket=pos，鍵名 backups/<檔名>）
#    ⚠️ 一律上傳「本次產生的確切檔名」（BACKUP_FILE 環境變數傳入），
#    不可用 glob 排序挑檔——目錄裡若有演練/還原殘檔會選錯（Codex P1）。
cd /home/test/lu-camp/backend && \
BACKUP_FILE=/home/test/lu-camp-backups/${DB}_${STAMP}.dump.enc uv run --with boto3 python - <<'PY'
import boto3, os
e = os.environ
f = e["BACKUP_FILE"]
assert os.path.isfile(f), f"備份檔不存在：{f}"
s3 = boto3.client("s3", endpoint_url=e["R2_ENDPOINT"],
    aws_access_key_id=e["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=e["R2_SECRET_ACCESS_KEY"], region_name="auto")
s3.upload_file(f, e["R2_BUCKET"], f"backups/{os.path.basename(f)}")
print("uploaded", f)
PY
```

## 2. 還原（下載 → 解密 → pg_restore → 驗證）

```bash
source /home/test/lu-camp/.env.r2
# 1) 下載（boto3 download_file 同上，鍵 backups/<檔名>）→ /home/test/lu-camp-backups/restore.dump.enc
# 2) 解密
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -in restore.dump.enc -out restore.dump -pass "pass:$R2_BACKUP_PASSPHRASE"
# 3) 還原到全新庫（絕不直接覆蓋正式庫；驗證後才切換）
"$DOCKER" exec lu-camp-db-1 psql -U lucamp -d postgres -c "CREATE DATABASE lucamp_restore"
cat restore.dump | "$DOCKER" exec -i lu-camp-db-1 sh -c 'cat > /tmp/r.dump'
"$DOCKER" exec lu-camp-db-1 pg_restore -U lucamp -d lucamp_restore /tmp/r.dump
```

**還原後必做四驗**（任一不過即不可切換使用）：

1. `DATABASE_URL=...lucamp_restore uv run alembic current` → 應為當前 head。
2. 關鍵表筆數 vs 備份當日紀錄（sales／signature_tasks／store_credit_ledger）。
3. 簽名影像完整性：抽 20 筆 `sha256(signature_image)` 與正式庫（或備份清單）比對。
4. 起一個指向 restore 庫的後端，登入＋開一頁報表確認可用。

> `alembic downgrade` **不是**還原手段；還原證據只認成功的 pg_restore（docs/26 DB-1 原則沿用）。

## 3. 演練紀錄（2026-07-16，對 lucamp_sim 200 天模擬資料集）

| 步驟 | 結果 |
|---|---|
| pg_dump（2.3MB，含 746 筆簽名 PNG） | ✓ sha256 `8e0a6afb4fdf64ca…` |
| openssl 加密 → 上傳 R2 `pos/backups/…` → 下載 | ✓ 上下行 sha256 一致 |
| 解密後 vs 原始 dump | ✓ sha256 完全一致 |
| pg_restore → `lucamp_sim_restore` | ✓ exit 0 |
| `alembic current` | ✓ `a2b3c4d5e6f7 (head)` |
| 簽名 PNG sha256 抽驗 20 筆 | ✓ 20/20 一致 |

R2 物件：`backups/lucamp_sim_pristine_20260716.dump.enc`（保留作異地副本）。
