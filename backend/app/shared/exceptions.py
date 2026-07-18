"""領域層自訂例外（禁止裸 except / 吞例外；router 將其對應為適當 HTTP 狀態）。"""


class DomainError(Exception):
    """所有領域錯誤的基底。"""


class StoreNotFound(DomainError):
    """指定的門市（store）不存在。"""


class InvalidMargin(DomainError):
    """定價 margin_pct 超出合法範圍（0-99）。"""


class InvalidDiscountPct(DomainError):
    """活動折扣 discount_pct 超出合法範圍（1-99）。"""


class CampaignConflict(DomainError):
    """門市活動衝突（同店已有生效活動、非法狀態轉移、區間/欄位不合法）。"""


class CampaignNotFound(DomainError):
    """指定的門市活動不存在（或非本店）。"""


class InvalidStateTransition(DomainError):
    """狀態機不允許的轉移（如已 SOLD 又要售出）。"""


class ItemNotAvailable(DomainError):
    """序號品非可售狀態（非 IN_STOCK / 已售出）。"""


class InsufficientStock(DomainError):
    """庫存不足（散裝批 remaining_qty 不足，扣減後會 < 0）。"""


class OwnershipValidationError(DomainError):
    """入庫資料與 ownership/grade 規則不符。"""


class CashSessionAlreadyOpen(DomainError):
    """同一 store 已有開帳中的 cash_session，不可重複開帳。"""


class NoOpenCashSession(DomainError):
    """影響現金的操作必須在開帳中的 cash_session 下進行，但目前無開帳。"""


class CashSessionAlreadyClosed(DomainError):
    """cash_session 已結帳，不可重複結帳（避免覆寫對帳結果）。"""


class UnknownCashMovementType(DomainError):
    """對帳時遇到未知的現金異動類型，拒絕靜默計算以免算錯現金。"""


class ContactNotFound(DomainError):
    """收購指定的 contact 不存在（或不屬於本店）。"""


class AcquisitionRequiresNationalId(DomainError):
    """收購/寄售對象必須有 national_id（接 T4：SELLER/CONSIGNOR 必填）。"""


class InvalidNationalId(DomainError):
    """national_id 格式/檢核碼不正確（避免手動輸入錯誤）。訊息不含輸入值（PII）。"""


class InvalidAcquisitionCategory(DomainError):
    """收購品項的 category_id 不屬於本店（F6 additive 持久化的租戶守衛）。"""


class DuplicateContact(DomainError):
    """編輯 national_id 時與同店他人既有 blind index 撞重（重複建檔，docs/17 §4.3）。"""


class MemberRemovalBlocked(DomainError):
    """contact 仍持有購物金帳戶/帳本時不可移除 MEMBER 角色（會留下非會員購物金負債，I-8）。"""


class InvalidCommissionPct(DomainError):
    """寄售抽成 commission_pct 超出合法範圍（0-100）。"""


class InvalidTaxRate(DomainError):
    """稅率超出合法範圍（須 0 ≤ rate < 1）。"""


class EmptySale(DomainError):
    """銷售單沒有任何明細行，無法結帳。"""


class SaleItemNotFound(DomainError):
    """銷售明細指向的商品不存在（或不屬於本店）。"""


class SaleLineInvalid(DomainError):
    """銷售明細行內容不合法（型別與參照不符、數量 <= 0 等）。"""


class CrossStoreReference(DomainError):
    """交易引用了不屬於本店的對象（如他店的 contact / user），多分店資料隔離違規。"""


class SaleAlreadyVoid(DomainError):
    """銷售已作廢，不可重複作廢。"""


class SaleHasReturns(DomainError):
    """已（部分/全部）退貨的銷售不可作廢——退貨已處理庫存/退款，作廢會重複回補造成庫存失真。"""


class MemberPointsAdjustFailed(DomainError):
    """會員點數調整失敗（對象不存在或會使點數為負）——資料不一致，整筆交易應回滾。"""


class IdempotencyKeyConflict(DomainError):
    """同一 idempotency key 但購物車內容不同：拒絕，避免靜默丟掉新的結帳。"""


class InsufficientStoreCredit(DomainError):
    """購物金餘額不足以扣抵/沖回——永不負餘額（docs/16 I-2/I-6）。"""


class StoreCreditConflict(DomainError):
    """同來源分錄已存在且內容不同（冪等指紋不符，docs/16 I-5）。"""


class StoreCreditMemberRequired(DomainError):
    """購物金帳戶主體必須是會員（contacts.roles 含 MEMBER，docs/16 I-8）。"""


class InvalidPayoutSplit(DomainError):
    """收購撥款拆分不合法（SPLIT 現金部分須 >0 且 < 應付總額）。"""


class InvalidSaleTender(DomainError):
    """銷售收款明細不合法（Σ tenders ≠ total、金額非正、重複型別、購物金缺會員）。"""


class InvalidPremiumRate(DomainError):
    """溢價率設定不合法（min>max、或 premium 不在 [min, max]，docs/16 §6.1）。"""


class AcquisitionNotFound(DomainError):
    """指定的收購單不存在（或不屬於本店）。"""


class AcquisitionAlreadyVoid(DomainError):
    """收購已作廢，不可重複作廢（F6.5；冪等重送同 key 回既成，非此例外）。"""


class AcquisitionHasSoldItems(DomainError):
    """收購含已售出的庫存（序號品非 IN_STOCK／散裝批已部分售出），不可作廢（F6.5）。"""


class AcquisitionCreditSpent(DomainError):
    """該收購入帳的購物金已被花用，沖回會使餘額為負——擋作廢轉人工更正（F6.5，永不負餘額）。"""


class AcquisitionVoidUnsupported(DomainError):
    """此收購類型不支援作廢（F6.5 僅支援 BUYOUT／BULK_LOT）。

    寄售（CONSIGNMENT）入庫的寄售品仍屬寄售人，作廢須走寄售退貨＋結算反轉
    （invariant #7），與買斷對稱反轉不同，另立任務實作前一律擋下。
    """


class SettlementNotFound(DomainError):
    """指定的寄售結算不存在（或不屬於本店）。"""


class SettlementNotPending(DomainError):
    """寄售結算非 PENDING（已付款 PAID／已取消 CANCELLED），不可再付款（Phase 4）。

    付款以 settlement 列鎖＋狀態為準：重送/併發只一筆成功，其餘回此例外（不重複出帳）。
    """


class PurchaseOrderNotFound(DomainError):
    """指定的採購單不存在（或不屬於本店）。"""


class PurchaseOrderNotReceivable(DomainError):
    """採購單目前不可收貨（已收貨/關閉/仍為草稿）。"""


class InputInvoiceInvalid(DomainError):
    """進項發票資料不合法（號碼格式/金額）。"""


class InputInvoiceAlreadySet(DomainError):
    """該收貨單已登錄進項發票，不可重複登錄/覆寫。"""


class PurchaseOrderNotReceived(DomainError):
    """採購單尚未收貨，無法補登進項發票。"""


class InvalidPurchaseOrder(DomainError):
    """採購單內容不合法（空白、重複商品、金額非整數元等）。"""


class PurchaseOrderNotSubmittable(DomainError):
    """採購單目前不可送出（僅草稿可送出）。"""


class PurchaseOrderNotCancellable(DomainError):
    """採購單目前不可取消（僅草稿/已下單且尚未收任何貨可取消）。"""


class DuplicateSupplier(DomainError):
    """同店供應商名稱重複。"""


class SupplierNotFound(DomainError):
    """指定的供應商不存在（或不屬於本店）。"""


class SupplierInactive(DomainError):
    """供應商已停用，不可用於新採購單。"""


class DuplicateCatalogProduct(DomainError):
    """同店 SKU 重複（數量型商品上架）。"""


class StocktakeNotFound(DomainError):
    """指定的盤點單不存在（或不屬於本店）。"""


class StocktakeNotDraft(DomainError):
    """盤點單非 DRAFT（已確認），不可再確認（Phase 5；確認僅一次）。"""


class StocktakeLineInvalid(DomainError):
    """盤點確認的實點明細不合法（實點數為負、或商品不在本盤點單）。"""


class ReturnNotFound(DomainError):
    """指定的退貨單不存在（或不屬於本店）。"""


class ReturnSaleNotFound(DomainError):
    """退貨指定的原銷售單不存在（或不屬於本店）。"""


class ReturnLineInvalid(DomainError):
    """退貨明細不合法（不屬於原銷售、數量超出可退量、或暫不支援的品項型別）。"""


class ReturnConflict(DomainError):
    """退貨與目前狀態衝突（已全退、已作廢、或付款型態暫不支援）。"""


class MenuItemNotFound(DomainError):
    """指定的餐飲菜單品項不存在（或不屬於本店、已封存）。"""


class MenuItemUnavailable(DomainError):
    """餐飲菜單品項目前停售（is_available=False），不可加入結帳。"""


class DuplicateMenuItem(DomainError):
    """同店餐飲菜單品項名稱重複。"""


class InvoiceNotFound(DomainError):
    """指定的發票不存在（或不屬於本店）。"""


class EInvoiceQueueItemNotFound(DomainError):
    """指定的電子發票上傳佇列項目不存在（或不屬於本店）。"""


class EInvoiceQueueNotRetryable(DomainError):
    """佇列項目目前不可重送（僅 FAILED 可重送；PENDING/UPLOADED 不可）。"""


class EInvoiceSerializerNotReady(DomainError):
    """MIG XML 序列化尚未依官方 XSD 實作（T13 收尾階段；憑證/主機到位後才做）。

    禁止憑 docs/14 對照骨架或記憶硬寫欄位（CLAUDE.md §6、docs/14 §4、docs/18）。
    """


class EInvoiceDropError(DomainError):
    """拋檔到 Turnkey SRC 目錄失敗（非法檔名/路徑逃逸等）。訊息不含檔案內容。"""


class EInvoiceQueueNotDroppable(DomainError):
    """佇列項目目前不可拋檔（非 PENDING，或對應發票已作廢）。避免重複/無效上傳。"""


class EInvoiceResultNotApplicable(DomainError):
    """平台回執不適用於此佇列項目（尚未認領拋檔，不應有平台回執）。"""


class EInvoiceResultConflict(DomainError):
    """遲到且矛盾的平台回執（與既有終態不一致）：事件已留稽核、終態不變更。

    呼叫端（router/importer）應保留已寫入的回執事件（commit）再回報衝突，
    供對帳時區分「重複的成功回執」與「矛盾的遲到失敗」。
    """


class InvoiceNotIssued(DomainError):
    """折讓的原發票尚未正式開立（PENDING/不存在）——未開立不可折讓（§7 不變量 5）。"""


class AllowanceExceedsInvoice(DomainError):
    """折讓累計金額超過原發票總額——折讓不可超過原發票。"""


class DuplicateAllowanceForReturn(DomainError):
    """同一退貨單已有折讓，不可重複開立（以 return_id 唯一保護）。"""


class InvoiceIncompleteForIssue(DomainError):
    """發票缺少開立必要欄位（字軌號碼/開立日/開立時間/隨機碼），狀態機不得標成 ISSUED。

    MIG 4.1 F0401 明列 InvoiceNumber/InvoiceDate/InvoiceTime 為必填；配號/序列化待 T13 收尾，
    在這些欄位齊備前不允許把發票視為已正式開立。
    """


class EInvoiceActivationNotReady(DomainError):
    """電子發票尚未可正式啟用（XSD 序列化器/字軌配號未實作，T13 收尾）。

    現在打開 einvoice_enabled 只會讓每筆銷售建永遠無法核可的 PENDING 發票、佇列無限堆積，
    故在就緒前於 settings 層擋下開啟（關閉不受限）。
    """


class SignatureTaskNotFound(DomainError):
    """指定的簽署任務不存在（或不屬於本店）。"""


class SignatureTaskNotPending(DomainError):
    """簽署任務非 PENDING（已簽署/已作廢），不可再簽名或作廢。"""


class SignatureTaskInvalidated(DomainError):
    """簽署任務於簽名當下發現 ref 實態已失效（銷售作廢/退貨），任務已被作廢。

    與 SignatureTaskNotPending 區分：本例外代表「任務狀態已改為 CANCELLED、**須提交**」，
    router 不可 rollback（否則作廢遺失、任務繼續被手持端輪詢到；docs/23 K5 第五輪）。
    """


class InvalidSignatureImage(DomainError):
    """簽名影像不合法（非 base64 PNG、或大小超出限制）。"""


class SignatureTaskConflict(DomainError):
    """簽署任務建立衝突（併發重推撞「同店單一待簽」唯一索引/條款版本種子競態），請重試。"""


class InvalidKioskPayout(DomainError):
    """手持端撥款選擇不合法（僅限 現金/購物金 二選一，docs/23 D7；或該任務類型不收撥款選擇）。"""


class SignatureContentMismatch(DomainError):
    """收購內容（品項/金額/總額）與已簽切結的內容快照不符（docs/23 K4）：客人簽的必須就是
    這張收購——改了金額/品項就不可沿用舊簽署，須重新推送簽署。"""


class AmegoNotConfigured(DomainError):
    """Amego 光貿 API 憑證未設定（AMEGO_APP_KEY 環境變數／店家統編）。

    docs/24：App Key 走環境變數、不入 repo/DB；未設定時不可啟用電子發票，也不可送單。
    """


class AmegoTransportError(DomainError):
    """Amego API 呼叫失敗（網路/逾時/非 JSON 回應）——結果未知或未送達。

    呼叫端不得將此視為平台已受理或已拒絕；佇列列維持可重試狀態。訊息不含 App Key。
    """


class AmegoIssueFailed(DomainError):
    """Amego 明確拒絕本次上送（code≠0）：佇列已轉 FAILED、留 last_error 可重試。

    與 AmegoTransportError 區分：本例外代表平台**已回覆拒絕**（非結果未知）。
    """


class EInvoiceSettingsChanged(DomainError):
    """結帳當下的發票設定與 POS 觀察值不符（他端切換 einvoice_enabled 的 TOCTOU 空窗）。

    結帳整筆拒絕、無副作用；店員重新確認發票欄位（統編/載具/捐贈）後再送。
    """


class LinePayNotConfigured(DomainError):
    """LINE Pay 憑證未設定（Channel ID/Secret 缺）——不可呼叫 Offline API。訊息不含 Secret。"""


class LinePayTransportError(DomainError):
    """LINE Pay API 呼叫失敗（網路/逾時/非 JSON）——結果未知（可能已扣款或未送達）。

    呼叫端據 fail-closed 整筆回滾；重試須以同 orderId 先 check，避免重複扣款。訊息不含 Secret。
    """


class LinePayChargeFailed(DomainError):
    """LINE Pay 明確拒付/授權失敗（returnCode≠0000）或查詢非 COMPLETE。

    fail-closed：整筆銷售不成立、回滾（不得留下無付款的已完成單）。店員改用其他方式或重掃。
    """


class ManualRefundRequired(DomainError):
    """作廢/退貨含無 API 退款管道的收款（台灣Pay：店員於其 App 手動退款）。

    不得靜默完成作廢而讓客人仍被扣款（docs/30 §5）：須由店員先於台灣Pay App 退款、再帶
    manual_refund_ack=True 確認，系統才反轉。避免「已作廢卻未退款」。
    """


class LinePayRefundAmbiguous(DomainError):
    """LINE Pay 退款上次結果未定（durable 記錄為 PENDING：呼叫後崩潰/回應遺失）。

    不得盲目重試（可能已退款→重退造成超退，docs/30 §5 finding #1）：fail-closed，請店員至
    LINE Pay 後台確認該筆退款後再處理。
    """
