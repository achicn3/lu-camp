# Deferred Items（延後項目追蹤）

> 從審查（含 Codex adversarial-review）或實作中發現、經決議延後處理的真實問題。
> 每項註明來源、原因、預定處理時機，確保不被遺忘。完成後移除並於該 PR 註記。

## D-4（橫切任務，待排）— 敏感操作的當前授權重驗（auth-hardening）

- **來源**：T12 Codex adversarial-review（2026-06-06）對 void 授權的觀察。
- **問題**：所有端點的身分都由 JWT claims 解出、**不查 DB**（見 `core/deps.py` 明載設計）。
  故被降權／停用／轉店的使用者，在短效 token 到期前仍可執行敏感操作（如 void、settings PATCH、
  national-id 解密）。這是**全應用層級的 auth 設計議題、非 T12 bug**——void 與其他敏感端點一視同仁。
- **修法（統一、集中）**：在 `core/deps.py`/auth 集中加「敏感操作重載 actor、驗當前 DB 的
  `role==MANAGER` 且 `is_active`」的依賴，**套用到所有敏感端點**（void、settings PATCH、
  national-id 解密，及未來新增者），不逐一各補、避免補一漏二與不一致。
- **現有緩解**：CLAUDE.md §5「JWT 短效 + refresh」先限縮風險窗口。
- **建議時機**：**前端（T19/T20）與真錢交易上線之前**完成（見 `docs/07-roadmap.md`），避免帶著
  「全憑 JWT claim」設計進入使用者實際操作期。
- **再次回報（2026-06-11，pre-A adversarial review 第二輪 medium）**：登入端點上線後此風險
  窗口進入實際使用期——已簽發 token 的 role/store_id/is_active 在 `exp` 前不會反映 DB 變更。
  D-4 實作時一併涵蓋：敏感操作重查 DB（既定修法）、必要時 token 版本/撤銷機制、登入節流
  改共享儲存（多 worker 時）、`seed_dev_store.py` 補與 `seed_dev_user.py` 相同的環境防護。
- **狀態**：待排獨立橫切任務。Codex 之後每跑會再報此點（app-wide 設計），視為已處置已知項、非 blocker。

## D-3（設計筆記，待 Phase 4 退貨一起評估）— 銷售作廢 vs 發票作廢狀態建模

- **來源**：T12 Codex adversarial-review（2026-06-06）對 void 的建模點。
- **觀察**：目前 `void` 以 `invoice_status=VOID` 表示「銷售已作廢」，混用了「銷售作廢」與「發票
  作廢」兩種語意；且對 `NOT_ISSUED`（尚未開票）的銷售也標 VOID，語意上是「待作廢接縫」而非真正
  發票作廢。這是**刻意、有文件記載的接縫**（實體回退屬 Phase 4 退貨、發票作廢 XML 屬 T13/T14），
  非 bug；經使用者明確拍板維持現狀（Codex 建議「拒絕作廢 NOT_ISSUED」會使 void 在 T13 前永遠
  不可用，故不採納）。
- **未來評估**：若要把「銷售作廢狀態」與「發票狀態」拆開（例如新增 `SaleStatus.VOIDED`、或獨立的
  cancellation 狀態機），併入 **Phase 4 退貨（returns）** 一起設計——那時才有序號品回 IN_STOCK、
  散裝回補、寄售反轉、退現金等整套實體回退，狀態建模一次到位。
- **狀態**：設計筆記，現在不做。

## P-1（已實作於分支、暫不合併）— POS 購物金折抵可輸入金額 + 全額折抵

- **來源**：使用者需求（2026-06-23）。原設計購物金只能「全額折抵」；改為可輸入折抵金額。
- **已完成**：分支 `feat/pos-store-credit-amount`（commit `022c68a`，**未 push、未合併**）。
  - 收款模式由 現金/購物金/混合 三模式合併為 **現金 / 購物金折抵** 兩模式；
    折抵以「金額」輸入，現金腿自動補足（`cash = 應付 − 購物金`），折抵滿額即純購物金付款。
  - 新增「全額折抵」按鈕 → 自動帶入 `min(會員餘額, 可折抵上限, 應付總額)`。
  - 折抵面板凸顯「購物金可折抵 $X」與放大強調的「餘額 $Y」。
  - 前端防呆：折抵 > 餘額 或 > 可折抵上限（內用排除）即擋下並停用結帳；後端原已具備
    `InsufficientStoreCredit`(409)／`InvalidSaleTender`(422)。
- **驗證**：四道門全綠（tsc/eslint/vitest 243）；`pos-smoke` 23/23；手動驗證二手+餐飲同車的
  折抵上限/全額折抵/防呆/結帳。自我 code review 過（未跑 Codex，依使用者指示）。
- **狀態**：等使用者決定何時合併（可能與「新增會員驗證」等後續一起）。合併前需 rebase 最新 main。
  另注：`full-e2e-smoke.mjs` step 9 已更新到新 UI，但該腳本在不相關的 step 4（收購品牌
  autocomplete）會先卡住，故整支未跑完——非本功能問題。

---

## 已解決

### D-6 ✅ — 列印/踢櫃卸載到 worker thread（2026-06-08 解決，測 A 前置）

- **來源**：T15 Codex 複審（2026-06-08）對 `print.py` 同步呼叫阻塞 event loop 的觀察。
- **修法**：`/print/receipt`、`/print/detail`、`/print/einvoice`、`/drawer/open`、`/print/label`
  的同步裝置 I/O 改用 `anyio.to_thread.run_sync` 卸載，與 `/devices/status` 一致；真機網路列印/
  逾時不再阻塞事件迴圈、拖垮 `/health` 等。`DeviceError` 仍由 worker thread 如實傳回統一 handler。
- **測試**：既有 PaperOut→409 經 offload 仍成立。

### D-5 ✅ — 真機列印 writer 的 OSError → DeviceError 映射（2026-06-08 解決，測 A）

- **來源**：T15 Codex 複審（2026-06-08）第 3 輪對 `escpos_receipt.py` `writer.write()` 的觀察。
- **修法**：新增 `agent/drivers/escpos_network.py` 的 `NetworkEscposWriter`（真機 EPSON Network
  後端、lazy 連線），在**此 writer 邊界**把 escpos `DeviceNotFoundError`／送出階段 `TimeoutError`／
  其他 `OSError` 翻成 `DeviceOffline`／`DeviceTimeout`（與 `status_real.py` 對 socket 錯誤的處理
  對稱，ADR-010）。排版驅動 `escpos_receipt.py` 維持只懂排版、不碰網路語意（層級正確）。
- **測試**：`tests/test_escpos_network.py` 用會丟 `DeviceNotFoundError`/`TimeoutError`/`BrokenPipeError`
  的假 Network 驗證映射、且失敗仍關閉連線——免實機。
- **待辦**：仍需測 A 實機驗證後把 `validated_on_hardware` 標 `true`。

### D-2 ✅ — POS 結帳 idempotency（2026-06-06 解決，T12）

- **來源**：T11 Codex adversarial-review finding ①。
- **修法**：`POST /sales` 需 `Idempotency-Key` 標頭（min_length=1/max_length=80）；sales 加
  `idempotency_key` + `idempotency_fingerprint`（購物車 sha256）+ `(store_id, idempotency_key)`
  唯一約束。同 key 內容相同 → 回原單、不重跑副作用；內容不同 → `IdempotencyKeyConflict`→409。
  pre-check 與 router 的 IntegrityError handler 共用 `find_idempotent_replay`（避免修一條漏一條），
  handler 縮窄到只吞 idempotency 唯一約束違反。
- **測試**：`test_sales_api.py`（缺/空 key→422、同 key 不同內容→409、replay）、
  `test_sales_idempotency_concurrency.py`（真並行：同 key 同購物車只建一筆/只扣一次/只收一次、
  同 key 不同購物車一筆成功一筆 409）。

### D-1 ✅ — 現金異動 vs 關帳競態（2026-06-05 解決）

- **來源**：T11 Codex adversarial-review finding ②。
- **修法**：於 cashdrawer 單一寫入處 `record_movement` 以 `SELECT … FOR UPDATE` 鎖開帳中的
  `cash_session` 列，並讓 `close_session` 先鎖同一列並刷新到已提交狀態後才算 expected／轉 CLOSED；
  兩者對同列 row lock 互斥（DB 層原子，非先查狀態再插入）。關帳若先成，後到的現金異動因條件式
  查詢查不到 OPEN 而被拒（NoOpenCashSession）。**T6（MANUAL_ADJUST）/T7（BUYOUT_OUT）/
  T11（SALE_IN）的現金寫入都經 `record_movement` 這一處，故一次修好三處。**
- **測試**：`tests/integration/test_cashdrawer_close_movement_race.py` 真並行（asyncio.gather）關帳
  與插入 SALE_IN，斷言最終一致：SALE_IN 落地則 expected 必含它、否則被拒，不會落進已關閉 session。
