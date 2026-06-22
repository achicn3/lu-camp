# 22 — 資料備份與還原（每日 CSV＋JSON 邏輯備份）

本店單機營運，備份目標是「**可攜、可讀、可隨時無損匯回**」：每天把整個資料庫**每張表**
匯出成 **CSV ＋ JSON 兩種檔案**，放到以日期命名的資料夾，之後**定期 rsync/複製到其他硬碟**。

實作：`backend/app/scripts/data_backup.py`（不動任何 DB model，純讀 `Base.metadata` 泛型 dump/load）。

> 與 `pg_dump` 的關係：本檔是**邏輯備份**（跨工具可讀、可挑表、可塞進試算表）。
> 要做整機 binary 快照仍可另行 `pg_dump`，兩者不衝突、可並存。

## 輸出格式

```
<out>/<YYYY-MM-DD_HHMMSS>/
    manifest.json      # format_version、created_at、alembic_revision、各表列數
    <table>.json       # 無損還原來源（每表一檔，rows 陣列）
    <table>.csv        # 人工檢視／試算表副本（每表一檔）
```

無損保證（避免格式亂掉）：
- 金額／數值（`Numeric`）→ **字串**（不經 float，零失真）。
- 時間（`DateTime`/`Date`）→ **ISO 8601 字串**。
- 二進位（`LargeBinary`，若有）→ `{"__bytes_b64__": "..."}`（base64）。
- 陣列（PG `ARRAY`）、`JSONB` → 原樣保留。
- **PII 仍為密文**：`national_id_enc` 匯出的是加密後字串、`national_id_blind_index` 是 HMAC；
  備份檔本身不含明文身分證字號。備份檔請與 DB 同等保護（加密硬碟／受控存取）。

> **還原一律讀 JSON**（無損來源）。CSV 僅供人工檢視；用試算表打開不影響 JSON 還原。

## 備份（每日）

```bash
cd backend
# 預設輸出到 ./backups；可用 --out 或環境變數 BACKUP_DIR 覆寫
uv run python -m app.scripts.data_backup backup --out /srv/lu-camp/backups
```

### 排程（擇一）

- **Linux/WSL cron**（每天 03:00）：
  ```cron
  0 3 * * * cd /home/<user>/lu-camp/backend && \
    BACKUP_DIR=/srv/lu-camp/backups /home/<user>/.local/bin/uv run \
    python -m app.scripts.data_backup backup >> /var/log/lu-camp-backup.log 2>&1
  ```
- **Windows 工作排程器**：每日呼叫一支 `.bat`，內容同上（在 WSL 內執行）。

### 同步到其他硬碟（自行定期）

備份資料夾為純檔案，直接複製即可，例如：
```bash
rsync -a --delete /srv/lu-camp/backups/ /mnt/backup-hdd/lu-camp/backups/
```
建議保留多份歷史（依日期資料夾天然分版），異地再留一份。

## 還原

```bash
cd backend
# 1) 先確保 schema 在備份當時的版本（manifest.alembic_revision）
uv run alembic upgrade head
# 2) 還原（--truncate 會先清空所有表再灌；不加會在表非空時拒絕，避免覆蓋）
uv run python -m app.scripts.data_backup restore \
    --in /srv/lu-camp/backups/2026-06-22_030000 --truncate
```

防呆：
- **schema 版本不符**（manifest 的 `alembic_revision` ≠ 當前 DB）→ 拒絕還原，要求先把 schema
  對齊到相同版本；確知無誤可加 `--force` 繞過（風險自負）。
- **目標表非空且未加 `--truncate`** → 拒絕，避免不小心蓋掉現有資料。
- 還原後會自動把整數主鍵 sequence 推進到最大值，避免之後新增撞 PK。
- 整個還原在單一交易內，失敗即整批回滾。

## 測試

`backend/tests/integration/test_data_backup.py`：序列化無損（純單元）、每表產出 JSON＋CSV、
TRUNCATE 後還原型別無損、防呆（非空／版本不符）、備份唯讀、還原後可正常新增（sequence）。
