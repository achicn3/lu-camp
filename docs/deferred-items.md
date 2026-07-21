# Deferred Items（延後項目追蹤）

> 從審查（含 Codex adversarial-review）或實作中發現、經決議延後處理的真實問題。
> 每項註明來源、原因、預定處理時機，確保不被遺忘。完成後移除並於該 PR 註記。

## D-7（T13 收尾必辦）— 電子發票「開立」語意重對齊 + 證明聯接線 + G3 稅務定案

- **來源**：2026-07-02 端到端流程檢視（feat/einvoice-infra）。
- **(a) 開立語意重對齊（設計層）**：目前狀態機唯有平台 ProcessResult 成功才把發票轉
  ISSUED——在「字軌配號未實作」階段這是誠實且正確的（任何發票都不該是 ISSUED）。但台灣
  B2C 實務相反：**開立是結帳當下的本地行為**（自已配字軌區間取號、產隨機碼、當場交付證明聯
  或存載具），F0401 上傳是「存證」（48 小時內），上傳失敗不會取消開立。**T13 落地配號時必須
  把 ISSUED 移回結帳當下**（本地配號成功即開立），佇列 UPLOADED 降格為「存證完成」；
  VOID_PENDING→VOID（等 F0501 核可）語意亦須同步重審。不重對齊就上線會拿錯誤的模型運作。
- **(a2) importer 協議約定（Codex 第九輪收斂備註）**：T13 回執 importer 若不經 router 直呼
  `record_result`，必須沿用「衝突/不適用/欄位不齊也 commit 回執事件」的協議（事件永不回滾）、
  並自檔名（…-a{n}.xml）解出交付世代帶入 `delivery_attempt`。
- **(b) 證明聯列印接線**：硬體代理 `/print/einvoice`（InvoicePayload：字軌+隨機碼+QR AES）
  已就緒；缺後端 Invoice→InvoicePayload 橋接與 POS 開立當下的呼叫點。與 (a) 綁定實作。
  同時開放 `/sales` API 的載具/捐贈/統編欄位與 POS UI（現為停用佔位）。
- **(c) G3 稅務定案（找會計師，開票上線前必須）**：購物金付款目前把購物金當「支付工具」、
  發票開**全額**。若會計師把儲值金歸類為「售出時即開立」或「折抵屬折讓」，開票金額與時點
  都會變（docs/16 G3）。
- **(d) ✅ 已解（2026-07-02，確認非刻意）**：POS 掃碼補一般商品第三段 fallback（序號→散裝→SKU），
  後端新增 `GET /catalog-products/by-sku/{sku}`；瀏覽器煙霧 `pos-catalog-smoke.mjs` 10/10。
- **狀態**：擋於 `EInvoiceActivationNotReady`（序列化器就緒前 einvoice_enabled 不可開啟），
  故不影響現行營運；T13 動工時以本項為 checklist。

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

## D-8（報表退貨口徑，待排）— 營收/毛利不扣退貨 + 退貨不沖點數

- **來源**：2026-07-02 金流/報表全面檢視（同批發現的「寄售抽成計入已取消結算」已修：
  `commission_total_for_sales` 排除 CANCELLED 與 reclaim_needed）。
- **(1) 營收/毛利報表不扣退貨（High，潛伏）**：`margin_components` 只排 VOID，退貨行仍以全額
  計營收與成本——全退的單照樣貢獻毛利（與現金側 `sale_refund_out` 對不上）；退回序號品再售出
  會**重複計營收**。影響 R2/R6/C4/經營洞察/SC-5b（同源 margin_breakdown）。docs/19:105 原裁示
  「returns 上線後分欄顯示」未實作。修法：各段以 `return_lines` 已退數量/金額沖回＋報表分欄。
- **(2) 退貨不沖回會員點數（Medium）**：`void_sale` 以 awarded_points 沖回、`create_return`
  沒碰點數——全退後客人白拿點數。**口徑待裁示：按退款金額比例沖 vs 全退才沖**。
- **緩解**：4B 退貨 UI 已擱置（店內不會用），退貨僅能走 API → 兩項現為潛伏問題；
  退貨線因電子發票 G0401 折讓已接上，啟用前必須修。

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
