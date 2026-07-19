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

⚠️ **店外必須同時保管兩組金鑰，缺一備份即廢**（Codex 第三輪 P1）：

1. `.env.r2` 全檔（含 AES 口令）——沒有口令，R2 上的備份解不開。
2. repo 根 `.env` 的 **`PII_ENC_KEY`、`HMAC_KEY`、`SECRET_KEY`**——dump 裡的身分證是
   「用 PII_ENC_KEY 加密後」的密文、去重索引是「用 HMAC_KEY 算的」；整機滅失後若只還原
   資料庫而金鑰不同，**所有身分證永遠解不開、去重索引全部失效**。
   （店主手機備忘錄或紙本保險箱各抄一份；換金鑰時同步更新。）

## 1. 備份（dump → 加密 → 上傳）

```bash
set -euo pipefail   # 任一步失敗即中止——dump 失敗仍繼續加密/上傳＝回報成功卻還原不了
                    # 的假備份（Codex 第三輪 P1）
DOCKER="/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"
# set -a：source 的變數必須 export，否則後面的 Python 子行程拿不到 R2 憑證
# （乾淨 shell 會 KeyError、上傳靜默失敗；Codex 第二輪 P1）。
set -a; source /home/test/lu-camp/.env.r2; set +a
STAMP=$(date +%Y%m%d)
DB=lucamp            # 正式庫名；演練用 lucamp_sim

# 1) 容器內 dump（custom format，含 BYTEA 簽名影像）
"$DOCKER" exec lu-camp-db-1 pg_dump -U lucamp -Fc -d "$DB" -f /tmp/backup.dump
"$DOCKER" exec lu-camp-db-1 cat /tmp/backup.dump > /home/test/lu-camp-backups/${DB}_${STAMP}.dump
# 上傳前驗 dump 可讀（pg_restore --list 解析目錄；空檔/壞檔在此擋下）
"$DOCKER" exec lu-camp-db-1 pg_restore --list /tmp/backup.dump > /dev/null
test -s /home/test/lu-camp-backups/${DB}_${STAMP}.dump

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

> 本節**自足可執行**（災難復原時多半是從這裡開始的乾淨 shell；Codex 第二輪 P1）。

```bash
DOCKER="/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"
set -a; source /home/test/lu-camp/.env.r2; set +a
cd /home/test/lu-camp-backups

# 1) 從 R2 下載指定備份（改 KEY 為要還原的檔名；可先用 list_objects 檢視）
KEY=backups/<要還原的檔名>.dump.enc \
uv run --directory /home/test/lu-camp/backend --with boto3 python - <<'PY'
import boto3, os
e = os.environ
s3 = boto3.client("s3", endpoint_url=e["R2_ENDPOINT"],
    aws_access_key_id=e["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=e["R2_SECRET_ACCESS_KEY"], region_name="auto")
s3.download_file(e["R2_BUCKET"], e["KEY"], "/home/test/lu-camp-backups/restore.dump.enc")
print("downloaded", e["KEY"])
PY

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

---

## 4. 系統化（docs/31 B4）：程式化還原 + 卡控 + 逐功能演練 + 受控切換

docs/28 §1–§3 的**手動** runbook 已由系統實作（分支 `feat/backup-system`）：

- **還原後端 / 四驗**：`app/modules/backup/restore.py`（`SubprocessR2RestoreBackend`＝下載→解密→
  建 throwaway 全新庫→pg_restore；`SqlRestoreVerifier`＝四驗：alembic head 相符／關鍵表可查／
  簽名 BYTEA 抽驗／起後端 SELECT 1）。**絕不就地覆蓋正式庫**。
- **卡控入口**：`POST /api/v1/backup/restore`（MANAGER＋知情勾選＋打字確認＝該備份「檔名」）→
  背景還原到 `lucamp_restore_<時戳>`＋四驗，`restore_runs` 留痕（VERIFIED/FAILED＋四驗 JSONB）。
  儀表板 `/backup` 有還原卡（選備份→確認對話框→輪詢狀態→顯示四驗結果）。
- **受控切換**：四驗 VERIFIED 後，正式切換由**停機腳本** `scripts/switch-to-restore.sh
  <lucamp_restore_...>` 執行（改名 live→`lucamp_old_<時戳>` 保留可回退、restore→live）。App 不自動
  切換（PG 不許改名有連線的庫；單機中途失敗兩頭落空）。

### 4.1 逐功能還原演練（`app/scripts/restore_drill.py`，使用者指示 2026-07-19）

`uv run python -m app.scripts.restore_drill lucamp_sim`（需 source `.env`＋`.env.r2`）：備份→還原到
throwaway 庫→**逐功能比對 before（來源）vs after（還原）**，每個功能都須一致。

**2026-07-19 對 lucamp_sim（180 天模擬）實跑：23/23 全部一致 ✅ PASS**：

| 功能 | before = after |
|---|---|
| 交易 筆數 / 銷售額合計 / 明細 / 收款 | 5313 / 11,615,725 / 12011 / 5386 |
| 現金 班別 / 異動 | 201 / 6132 |
| 會員 筆數 / **PII 密文 md5** / **盲索引 md5** | 2264 / `6cf4f0a5…` / `514bd2d2…` |
| 庫存 序號品 / 散裝餘量 | 1520 / 0 |
| 簽署 任務 / **簽名 BYTEA md5** | 735 / `5d60be4e…` |
| 購物金 帳本 / 淨額合計 | 239 / 709,627 |
| 盤點 單 / 明細 | 8 / 240 |
| 寄售 結算 / 採購 單 / 收貨 | 259 / 94 / 116 |
| 發票 筆數 / 折讓 | 0 / 0 |
| 稽核 筆數 | 1586 |

密文/盲索引/簽名影像的 md5 完全一致 → 不只筆數對，**內容值（含加密 PII、BYTEA 簽名）逐位元組無損**。
throwaway 庫用畢即刪，正式資料全程未動。
