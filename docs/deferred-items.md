# Deferred Items（延後項目追蹤）

> 從審查（含 Codex adversarial-review）或實作中發現、經決議延後處理的真實問題。
> 每項註明來源、原因、預定處理時機，確保不被遺忘。完成後移除並於該 PR 註記。

## D-1（緊接 T11 之後的下一個任務）— 現金異動 vs 關帳競態

- **來源**：T11 sales 的 Codex adversarial-review（2026-06-05）finding ②。
- **問題（真實現金併發漏洞）**：`cashdrawer.record_movement` 與 `close_session` 之間有競態。
  `record_movement` 先 `get_open_session`（讀到 OPEN）後插入 `cash_movement`；同時另一交易的
  `close_session` 可能已計算 `expected_amount` 並把 session 轉 `CLOSED` 並 commit。結果該筆
  現金異動落進「已關閉」的 session，且關帳對帳的 expected/variance 漏算這筆 → 帳不平。
- **影響範圍**：屬 cashdrawer（T6）既有設計，**T7（BUYOUT_OUT）與 T11（SALE_IN）皆受影響**，
  非 T11 新增。故在 cashdrawer 模組統一修，一次修好 T6/T7/T11。
- **修法（DB 層原子保證，不得先查狀態再插入）**：鎖 `cash_session` 列（`SELECT … FOR UPDATE`），
  或以條件式 guard 僅允許 `status='OPEN'` 時插入現金異動（比照 T5/T6 的條件式 UPDATE 原子做法）；
  關帳同樣需與插入互斥。
- **驗收**：補真並行測試（`asyncio.gather`）——關帳的同時插入一筆 `SALE_IN`，證明不會落進已關閉
  的 session（要嘛被擋、要嘛被計入 expected，不得兩頭落空）。
- **狀態**：待辦（T11 合併後緊接著做）。

## D-2（T12 必做）— POS 結帳 idempotency

- **來源**：T11 sales 的 Codex adversarial-review（2026-06-05）finding ①。
- **問題**：`SalesService.create_sale` 每次呼叫都新建一筆 sale 並重跑所有副作用（扣庫存、收現）。
  屬領域層正確行為；但一旦有 `POST /sales`（T12），網路重試／回應遺失會重複建單、重複收錢、
  重複扣庫存（CATALOG/BULK_LOT 純數量購物車尤其無從以序號狀態機擋下）。
- **T12 必做**：`POST /sales` 接受呼叫端產生的 idempotency key；以 `(store_id, idempotency_key)`
  唯一約束持久化，重試時回傳原結果而非重複建單。補「已提交後重試」整合測試。
- **狀態**：待辦（T12 實作 sales API 時必做，勿遺漏）。
