# 線上點餐營運防護（2026-10-14）

## 行為與邊界

- 現金線上單從客人送出時間起算 10 分鐘未結帳，自動設為 CANCELLED／VOIDED，釋放仍有效的限量保留；不限量單也取消。以訂單鎖與結帳互斥，重跑不重複回補，留下 AUTO_CANCEL_ONLINE_ORDER 稽核。
- POS 每 5 秒掃描；POS 停機時待恢復後取消，雲端透過持久化回報佇列更新。不能保證 POS 離線時客人頁準時顯示取消。付款處理中或結果不明時暫緩，避免誤取消已扣款單。
- POS「餐飲菜單 → 線上發布 → 營業時間」可設定一週七天單一時段，公休日不接新單；時間採台北時區，結束早於開始表示跨午夜。設定預設停用，沿用手動接單；開關不影響手動暫停的優先權。儲存後須再按「發佈到線上點餐」。不含國定假日與單日多時段。
- Worker 依已發佈時段拒絕新訂單；既有冪等重試、查單和付款完成不受關店影響。
- LINE Pay PENDING／CONFIRMING 自本次發起付款滿 10 分鐘，出現在 POS「付款待確認」清單。補查不重設起算點；狀態解除後下次成功同步清除。最多顯示最早 50 筆並保留總數，顯示訂單與交易號方便對帳。提醒不自動取消或宣稱已付款，店員應核對金流後處理。

## 升級順序

1. 僅 staging Worker：先 `wrangler d1 migrations apply lu-camp-online-order-staging --remote` 套用 `0009_payment_started_at.sql`，再 `wrangler deploy`。既有菜單缺少 hours 欄位時保持原本手動接單行為。
2. POS 升級前備份 PostgreSQL，停服務後 `uv run alembic upgrade head`（新 head `d9f3b7a1e5c8`，前版 `a7e3c9f1b5d2`），再啟動相同版本 backend／frontend。migration 新增營業時段及同步狀態欄位，沒有自動取消資料。
3. 核對背景拉單與回報正常，再設定並發佈營業時間。啟用新版 backend 後，既存逾時未付款現金單也會被取消。店內正式機尚未執行本次升級。
4. Worker 回退可保留新增 D1 欄位；POS 回退先停服務並回退程式。已取消的訂單不能以程式回退復活，需另開新單。

## 實際驗收

`frontend/scripts/online-operating-guards-smoke.mjs` 只連 localhost :8116／:3116／:8794，要求 `SMOKE_ALLOW_WRITE=1` 且 PostgreSQL 庫名含 `_e2e`。

- 真 POS UI 儲存並發佈全週公休 → 手機頁提示非營業時間，直接送單 HTTP 503。
- 解除時段限制 → 手機實際送出限量現金單 → 真 PostgreSQL 保留。
- 使用 service 公開的 `now` 參數推進掃描時鐘（不等待 10 分鐘）→ 取消，庫存恢復，客人頁及 POS 同步取消，POS 不再提供帶入結帳。
- 隔離 D1 注入模擬 LINE Pay 待確認狀態 → Worker 拉單 → PostgreSQL → POS 顯示交易號；解除狀態後提醒消失。這不是 LINE Pay 真金流驗收。
- 390px 手機 POS 設定畫面無橫向溢出，截圖已檢視。
- 共用選項另以 `online-shared-options-smoke.mjs` 驗證 375／390／430px：等待菜單自動刷新後，「不選擇」仍可用且保持選取；送單後 POS 兩行 170 + 150 = 320，取消加價正確。
- [驗收截圖](screenshots/2026-10-14-operating-guards/)。

完整資安與真金流驗收仍依 [交接文件](50-online-ordering-handoff.md)／[資安驗收紀錄](79-online-order-security-audit.md) 的未完成項目執行，不能以此次功能測試代替。
