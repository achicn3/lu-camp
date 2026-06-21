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


class InvalidPurchaseOrder(DomainError):
    """採購單內容不合法（空白、重複商品、金額非整數元等）。"""


class DuplicateSupplier(DomainError):
    """同店供應商名稱重複。"""


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
