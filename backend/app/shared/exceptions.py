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
