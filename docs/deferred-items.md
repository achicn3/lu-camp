# Deferred Items（延後項目追蹤）

> 從審查（含 Codex adversarial-review）或實作中發現、經決議延後處理的真實問題。
> 每項註明來源、原因、預定處理時機，確保不被遺忘。完成後移到「已解決」並註明證據。

## D-7（僅 G3 待外部裁示）— 電子發票與購物金稅務口徑

- **來源**：2026-07-02 自建 Turnkey 路線的端到端流程檢視；2026-07-09 改採 Amego API。
- **(a) ✅ 舊開立語意已被取代**：系統不再自行配字軌／上傳 Turnkey F0401。Amego 成功回傳
  字軌、隨機碼與條碼／QR 後才把發票標為 `ISSUED`；傳輸結果不明則維持可對帳狀態，不假裝成功。
  舊設計「本地配號即開立、上傳只是存證」不適用於目前 Amego 委外配號模式。
- **(a2) ✅ 舊 importer 協議不再是正式路徑**：正式上送／查詢／重試由 Amego 狀態機處理；
  歷史 Turnkey outbox 若已被舊通道認領，會拒絕跨通道送 Amego 並要求人工對帳。
- **(b) ✅ 證明聯與 POS 已接線**：銷售 API/POS 已支援 B2B、載具與捐贈；結帳後呼叫 Amego，
  無載具且未捐贈時把平台回傳內容送 `/print/einvoice`。真 Amego、fake agent 與 EPSON 實機皆驗過，
  詳見 docs/24 與 `einvoice-smoke.mjs`。
- **(c) 尚待 G3 會計師裁示**：購物金目前視為支付工具、發票開銷售全額。若儲值金應於售出時
  開票，或折抵應視為折讓，開票金額與時點都需調整；上正式金流／發票前必須定案（docs/16 G3）。
- **(d) ✅ 一般商品 SKU fallback 已解**：`GET /catalog-products/by-sku/{sku}` 與 POS 第三段
  fallback 已有測試與瀏覽器 smoke。
- **狀態**：電子發票應用功能已完成；本項只保留外部會計政策 G3，不再以 T13/Turnkey 工作阻擋。

## D-3（刻意保留的設計接縫）— 銷售作廢 vs 發票作廢狀態建模

- **來源**：T12 Codex adversarial-review（2026-06-06）對 void 的建模點。
- **觀察**：目前 `void` 以 `invoice_status=VOID` 表示「銷售已作廢」，混用了「銷售作廢」與「發票
  作廢」兩種語意；且對 `NOT_ISSUED`（尚未開票）的銷售也標 VOID，語意上是「待作廢接縫」而非真正
  發票作廢。這是**刻意、有文件記載的接縫**，不是尚未完成 Phase 4 或 Amego：現行 void 已處理
  庫存／點數／購物金與 LINE Pay／寄售反轉及平台作廢，returns 另有 `SaleStatus.RETURNED` 狀態機；但整筆 sale
  void 仍以 `invoice_status=VOID` 表示。
- **未來評估**：只有在產品需要把「銷售取消」與「發票作廢」呈現為兩個獨立生命週期時，才新增
  `SaleStatus.VOIDED` 或 cancellation 狀態機；遷移時須同時調整報表、簽署、退貨與金流守衛。
- **狀態**：已接受的設計接縫，不阻擋目前功能；不因 Phase 4 已完成而自行改動資料模型。

---

## 已解決

### D-8 ✅ — 主帳務報表扣退貨＋退貨按比例沖點（2026-07-16 解決）

- **修法**：`ReturnsService.margin_adjustments` 依退貨發生日與退貨行比例，從
  `margin_breakdown` 的營收／成本桶扣回；散裝成本使用累計差額法，避免除不盡時少沖成本。
  會員點數依非餐飲退款占原非餐飲小計的累計比例沖回，已花掉時 clamp 至現有點數，不阻擋退貨。
- **涵蓋**：R2/R5/R6/C4 共用主帳務口徑與交易紀錄退貨 UI 已完成；測試見
  `backend/tests/integration/test_returns_d8.py`。
- **已接受的分析限制**：SC-5 建議引擎 `period_margin`、品牌／類型 `insights` 不扣退貨；寄售跨期
  退貨抽成沿既有結算狀態。三者為分析用途、非主帳務，200 天資料量化影響約全期營收 0.05%，
  依 2026-07-16 裁示文件化而不追加實作。

### D-4 ✅ — 每次請求重驗當前授權（2026-06-18 解決）

- **修法**：`core/deps.get_current_user` 不再以 JWT role/store claim 當現況；每次請求以 token 的
  user/store 識別重查 DB，停用／刪除立即 401，降權立即依 DB role 生效，KIOSK 亦獨立重驗。
- **涵蓋**：所有使用共用依賴的端點一次收斂，包含作廢、設定、PII 查看與後續新增端點；不是逐一路由
  補洞。測試見 `backend/tests/integration/test_deps_revalidation.py`。
- **剩餘產品裁示**：已掛載中的管理頁不在使用者被即時降權時主動清畫面／重導；任何後端讀寫仍
  立即 403。此單店情境已於 2026-07-17 明確接受並文件化，不重開 D-4。

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
