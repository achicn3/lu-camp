# Deferred Items（延後項目追蹤）

> 從審查（含 Codex adversarial-review）或實作中發現、經決議延後處理的真實問題。
> 每項註明來源、原因、預定處理時機，確保不被遺忘。完成後移除並於該 PR 註記。

## D-2（T12 必做）— POS 結帳 idempotency

- **來源**：T11 sales 的 Codex adversarial-review（2026-06-05）finding ①。
- **問題**：`SalesService.create_sale` 每次呼叫都新建一筆 sale 並重跑所有副作用（扣庫存、收現）。
  屬領域層正確行為；但一旦有 `POST /sales`（T12），網路重試／回應遺失會重複建單、重複收錢、
  重複扣庫存（CATALOG/BULK_LOT 純數量購物車尤其無從以序號狀態機擋下）。
- **T12 必做**：`POST /sales` 接受呼叫端產生的 idempotency key；以 `(store_id, idempotency_key)`
  唯一約束持久化，重試時回傳原結果而非重複建單。補「已提交後重試」整合測試。
- **狀態**：待辦（T12 實作 sales API 時必做，勿遺漏）。

---

## 已解決

### D-1 ✅ — 現金異動 vs 關帳競態（2026-06-05 解決）

- **來源**：T11 Codex adversarial-review finding ②。
- **修法**：於 cashdrawer 單一寫入處 `record_movement` 以 `SELECT … FOR UPDATE` 鎖開帳中的
  `cash_session` 列，並讓 `close_session` 先鎖同一列並刷新到已提交狀態後才算 expected／轉 CLOSED；
  兩者對同列 row lock 互斥（DB 層原子，非先查狀態再插入）。關帳若先成，後到的現金異動因條件式
  查詢查不到 OPEN 而被拒（NoOpenCashSession）。**T6（MANUAL_ADJUST）/T7（BUYOUT_OUT）/
  T11（SALE_IN）的現金寫入都經 `record_movement` 這一處，故一次修好三處。**
- **測試**：`tests/integration/test_cashdrawer_close_movement_race.py` 真並行（asyncio.gather）關帳
  與插入 SALE_IN，斷言最終一致：SALE_IN 落地則 expected 必含它、否則被拒，不會落進已關閉 session。
