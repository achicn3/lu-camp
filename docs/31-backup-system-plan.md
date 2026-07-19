# 31 — 備份系統（自動排程 + 儀表板 + 卡控還原）實作計畫

> 基線：`main` @ `33fb18e`（付款方式 epic 已合併）。分支 `feat/backup-system`。
> 前置：docs/28 runbook（pg_dump→AES→R2 手動流程＋還原＋演練，全驗證過）、docs/27 裁示
> （簽名等全資料以 Postgres 為主儲存、異地備份＝整庫 pg_dump 加密上 R2）。
> 決策依據：CLAUDE.md（TDD／分層／§4 多分店就緒／§5 PII／§10 git）、backup-stage 記憶裁示。

---

## 1. 範圍與裁示（已定）

把 docs/28 的**手動**pg_dump→AES→R2 流程,做成**系統**:

1. **自動排程**(到期驅動,非固定時間)——見 §3。
2. **儀表板**(店務 UI 新頁):設定(間隔/保留份數/離峰窗)、健康度(上次成功/落後告警)、
   備份清單(日期/大小/sha256/狀態)、手動觸發。
3. **卡控還原**:還原到**全新庫**+ 自動四驗(alembic/表數/簽名 sha256/起後端),強卡控入口
   (MANAGER + 打字確認 + 還原前先備份當下);最終「切換到還原庫」(repoint + 重啟)為受控腳本步驟
   ——**絕不在 app 連著正式庫時就地覆蓋它**(docs/28 §2 原則)。

**裁示**(backup-stage 記憶,2026-07-16):
- 不備份到行動硬碟(R2 保留 30 份、口令店外抄存、定期演練)。
- **R2 成本紀律**:每日至多 1 傳(免費額度綽綽有餘);舊檔刪除不收費。
- **兩組金鑰缺一即廢**(docs/28 §0):`.env.r2`(含 AES 口令)+ repo `.env` 的
  `PII_ENC_KEY`/`HMAC_KEY`/`SECRET_KEY` 必須店外同時保管。系統健康度頁**明白提示此事**。
- **假備份是最大風險**:任一步失敗(dump/驗證/加密/上傳)= 該次備份**失敗**、不得記成功
  (`set -euo pipefail` 精神,狀態表如實記 FAILED + last_error)。

**排程機制裁示(本次對話 2026-07-18)**:採**到期驅動的行程內 tick**,不依賴登入/session/開店訊號
(本系統永不過期登入,24/7 不關機時無「開店」訊號)。見 §3。

---

## 2. 資料模型(需 migration)

- **新表 `backup_runs`**(每次備份一列,對帳/健康度/清單來源):
  `id / store_id / trigger(SCHEDULED|MANUAL) / status(RUNNING|SUCCEEDED|FAILED) /
  started_at / finished_at / db_name / file_name / r2_key / size_bytes / sha256 /
  last_error(text,null) / actor_user_id(手動觸發者,null=排程)`。
  - **單一在跑守衛**:同店至多一列 RUNNING(部分唯一索引 `WHERE status='RUNNING'`)——tick 與手動
    撞在一起只一個進行,另一個看到 RUNNING 即跳過(併發保護,不靠應用層旗標)。
- **新表 `restore_runs`**(還原稽核/結果):`id / store_id / source_r2_key / restore_db_name /
  status(RUNNING|VERIFIED|FAILED) / started_at / finished_at / verifications(JSONB,四驗結果) /
  last_error / actor_user_id`。還原屬高危,一律留痕。
- **`settings` 新增**:`backup_enabled(bool,預設 true)`、`backup_interval_hours(int,預設 24)`、
  `backup_retention(int,預設 30)`、`backup_offpeak_hour(int 0–23,預設 4=凌晨4點後算到期)`。
  (比照既有 settings 慣例:server_default + defaults.py + schemas 驗證。)
- 敏感:`backup_runs`/`restore_runs` **不含 PII、不含金鑰、不含 R2 憑證**;僅檔名/雜湊/大小/狀態。

---

## 3. 排程(到期驅動的行程內 tick)

**觸發條件**(與 session/登入/開關機無關):`now − 上次 SUCCEEDED 備份 ≥ backup_interval_hours`
**且**已過今日離峰時點(`now.hour ≥ backup_offpeak_hour`,或已落後超過 grace 則強制補)。

**檢查時機**:FastAPI lifespan 內起一個輕量 asyncio 背景任務,每 ~15 分鐘醒一次做上述判斷。
- 24/7 不關機 → tick 於凌晨離峰醒來、發現到期 → 離峰備份。
- 晚上關機 → tick 隨後端開機起來、發現已落後 → 開機後補跑(接受落在營業時間;一天一次、~3MB、影響小)。
- 後端沒起來(沒人開 POS)→ 不備份,但**健康度頁顯示「上次備份 X 天前」+ 告警**(安全網)。

**單一在跑**:tick 先查/插 `backup_runs` RUNNING(部分唯一索引 + `ON CONFLICT DO NOTHING` 或先查);
撞到既有 RUNNING 即跳過。備份執行不阻塞請求(背景任務,自己的 session)。

**可停用**:`backup_enabled=false` → tick 不觸發(但手動仍可)。

---

## 4. 備份執行器(docs/28 流程 + 狀態表 + 稽核)

`BackupService`(service 層):一次備份 =
1. 插 `backup_runs` RUNNING(撞單一在跑守衛則放棄)。
2. `docker exec pg_dump -Fc` → 落地 → `pg_restore --list` 驗可讀 → 檔案非空。
3. AES-256-CBC + PBKDF2 20 萬次加密。
4. 計 sha256、大小。
5. boto3 上傳 R2 `backups/<檔名>`(憑證自 `.env.r2`,不入 DB/log)。
6. 修剪保留份數(R2 + 本地,超過 retention 刪最舊;刪除不收費)。
7. 更新該列 SUCCEEDED(+ file_name/r2_key/size/sha256/finished_at);任一步失敗 → FAILED + last_error。
- 外部呼叫(docker/openssl/boto3)以**可注入替身介面**包起來(比照 amego/linepay 的 Transport),
  單元測試以假替身驗流程與狀態機,不真的 dump/上傳。
- 寫 `audit_log`(BACKUP_RUN)。R2 成本紀律:排程每日至多 1 次(到期條件天然節流);手動由店長負責。
- **整庫備份為全域**(一次 dump 含所有分店):保留份數修剪一律取**主店(最小 store_id)**設定,不因
  觸發店而異——否則次要店的 retention＋手動備份會刪掉全域復原點(Codex 對抗審 #5)。多店上線時,
  備份政策/擁有權應正式建為全域管理員專屬。離峰鐘點以 `backup_timezone`(預設 Asia/Taipei)當地時區
  判定,非伺服器 UTC(Codex #6)。
- **檔名含短 UUID**(`{db}_{時戳}_{uuid8}.dump.enc`):即使同秒觸發也不會覆蓋同名 dump/R2 key,
  杜絕碰撞(Codex 對抗審第二輪 #3)。**全域併發序列化**(跨店同時備份)留待多店上線再做(DB advisory
  lock/單例約束);單店單機由 per-store 單飛守衛已足。
- **修剪順序**:SUCCEEDED 中繼資料**先 commit,再修剪**(修剪為不可逆刪除,放獨立 best-effort 步驟);
  否則 prune 後若 commit 失敗會刪掉尚未持久化其替代品的復原點(Codex 對抗審第二輪 #1)。

---

## 5. 儀表板(店務 UI 新頁 `/backup`,MANAGER)

- **健康度**:上次成功備份時間/落後天數、下次到期、**兩組金鑰店外保管提醒**(醒目)、enabled 狀態。
- **設定**:間隔/保留/離峰窗/啟用(PATCH settings,沿溢價率頁的表單慣例)。
- **清單**:近 N 次 `backup_runs`(日期/大小/sha256/狀態/觸發者);FAILED 顯示 last_error。
- **手動觸發**:「立即備份」按鈕(背景執行,輪詢狀態)。
- **還原入口**(見 §6):列 R2 可還原檔、強卡控。

---

## 6. 卡控還原(還原到全新庫 + 四驗;切換為受控腳本)

- Dashboard 選一份 R2 備份 → **MANAGER + 打字確認字串 + 「還原前先備份當下」勾選** → 觸發:
  下載 → 解密 → `CREATE DATABASE lucamp_restore_<stamp>` → pg_restore → **四驗**
  (alembic current=head、關鍵表數、簽名 sha256 抽驗、起後端可用)→ `restore_runs` 記 VERIFIED/FAILED。
- **只還原到 throwaway 庫並驗證,絕不動正式庫**。四驗全過才顯示綠燈 + 「如何切換」指引。
- **最終切換**(repoint DATABASE_URL + 重啟後端指向還原庫)=**受控腳本 / 手動步驟**,不由 app 自動做
  (app 不能一邊連正式庫一邊把自己換掉;單機中途失敗兩頭落空)。docs/28 §2 已有手動步驟,本輪把
  「下載→解密→restore→四驗」自動化 + 卡控 + 留痕,切換維持腳本。

### 6.1 還原驗收:逐功能實測「救得回且結果符合預期」(使用者指示 2026-07-19,B4 必做)

不只查表數/雜湊——B4 完成後,**必須逐一實測系統上每個功能的資料都成功還原且結果符合預期**。
做法:對還原庫**起一個後端**,逐功能比對「備份當下 vs 還原後」:

| 功能 | 驗什麼(還原後 = 備份當下) |
|---|---|
| 交易紀錄(sales) | 逐單金額/明細/收款/發票狀態;抽數筆逐欄比對 |
| 現金對帳(cash_session/movement) | 班別開/關帳、各現金異動、應有現金公式結果 |
| 會員/賣方(contacts) | 姓名/電話/角色/點數;**PII 密文可用還原後金鑰解出明文**(金鑰同組才行→印證兩組金鑰缺一即廢) |
| 庫存(serialized/bulk/catalog) | 序號品狀態、散裝 remaining、數量品現量 |
| 簽署紀錄(signing_tasks) | 任務狀態、內容;**簽名 PNG(BYTEA)sha256 逐筆一致**(影像無損) |
| 購物金/點數(store_credit_ledger) | 帳本每筆分錄、各會員餘額 |
| 盤點(stocktake) | 盤點單、差異、調整 |
| 寄售結算(consignment_settlement) | 各結算狀態/金額/reclaim |
| 採購/進項(purchase_orders/invoices) | 採購單、收貨、進項發票 |
| 電子發票(invoices/queue) | 開立/作廢/折讓狀態、字軌 |
| 財務報表(reports) | 對還原庫跑毛利/日結/趨勢,數字與備份當下一致 |
| 稽核(audit_log) | 敏感操作留痕完整 |

以**還原演練腳本 + 逐功能斷言**落實(比對備份前後的關鍵查詢結果);全部通過才算 B4 驗收過,
並附證據(每功能 before/after 一致)。這是「災難時真的救得回、且沒有任何遺失/走樣」的實證。

---

## 7. 實作波次(TDD;每波四門 + 停下確認)

- **B1**:`backup_runs`/`restore_runs` model + migration + settings 欄 + `BackupService` 執行器
  (可注入 docker/openssl/R2 替身)+ 狀態機 + 稽核 + 單元測試。**先落地核心與資料模型。**
- **B2**:排程 tick(lifespan 背景任務、到期判斷、單一在跑守衛)+ 測試。
- **B3**:儀表板 UI(健康度/設定/清單/手動觸發)+ 端點 + 契約 + 瀏覽器 e2e。
- **B4**:卡控還原(下載→解密→restore→四驗自動化 + 入口卡控 + `restore_runs`)+ 切換腳本 + docs/28 更新。
- 涉外部程序/金鑰/災難復原 → 高風險,合併前走 Codex adversarial-review。
