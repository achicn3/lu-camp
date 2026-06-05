"""領域層自訂例外（禁止裸 except / 吞例外；router 將其對應為適當 HTTP 狀態）。"""


class DomainError(Exception):
    """所有領域錯誤的基底。"""


class InvalidMargin(DomainError):
    """定價 margin_pct 超出合法範圍（0-99）。"""


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


class InvalidCommissionPct(DomainError):
    """寄售抽成 commission_pct 超出合法範圍（0-100）。"""


class InvalidTaxRate(DomainError):
    """稅率超出合法範圍（須 0 ≤ rate < 1）。"""
