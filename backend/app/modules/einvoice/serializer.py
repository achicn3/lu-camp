"""MIG XML 序列化器介面（seam）。

**刻意保留未實作**：實際 F0401/F0501/F0701/G0401/G0501 的欄位名稱、順序、資料長度、
Enum、民國年格式、RandomNumber 規則，必須依**當時下載的官方 MIG 4.1 XSD 與 Turnkey 3.9
手冊**逐欄落地，並對真 Turnkey 驗證後才可上線（CLAUDE.md §6、docs/14 §4、docs/18）。
禁止憑 docs/14 對照骨架或記憶硬寫。

因此本檔只定義 Protocol 與一個明確拋出 `EInvoiceSerializerNotReady` 的預設實作，讓佇列/
拋檔流程可先完整建置與測試，序列化本體待收尾階段替換為 XSD-backed 實作。
"""

from typing import Protocol, runtime_checkable

from app.modules.einvoice.models import Invoice, InvoiceAllowance
from app.shared.enums import EInvoiceMessageType
from app.shared.exceptions import EInvoiceSerializerNotReady

# 電子發票是否可正式啟用：XSD-backed 序列化器與字軌配號落地（T13 收尾）後改 True。
# settings 以此擋 einvoice_enabled=true 的開啟（避免佇列堆積出永遠無法核可的 PENDING 發票）。
XSD_SERIALIZER_READY = False


@runtime_checkable
class InvoiceXmlSerializer(Protocol):
    """把本地發票/折讓紀錄序列化為某訊息類型的 MIG XML bytes（UTF-8）。

    **必須確定性**（同一發票列 → 位元組完全相同）：兩階段拋檔的 crash 恢復以認領時的
    sha256 驗證重算內容（einvoice/service.drop_pending），非確定性輸出（如內嵌當下時間戳）
    會使恢復被拒。時間類欄位一律取自發票列（invoice_date/invoice_time），不得取 now()。
    """

    def serialize_invoice(self, invoice: Invoice, message_type: EInvoiceMessageType) -> bytes:
        """開立/作廢/註銷（F0401/F0501/F0701）的 XML bytes。"""
        ...

    def serialize_allowance(
        self, allowance: InvoiceAllowance, message_type: EInvoiceMessageType
    ) -> bytes:
        """折讓/折讓作廢（G0401/G0501）的 XML bytes。"""
        ...


class DeferredXmlSerializer:
    """預設序列化器：一律拋 `EInvoiceSerializerNotReady`。

    在拿到官方 XSD 並完成 XSD-backed 實作前，任何實際序列化嘗試都應明確失敗，
    而非產出可能錯誤的 XML。
    """

    _MSG = "MIG XML 序列化待 T13 收尾階段依官方 XSD 實作（見 docs/14 §4、docs/18）"

    def serialize_invoice(self, invoice: Invoice, message_type: EInvoiceMessageType) -> bytes:
        raise EInvoiceSerializerNotReady(self._MSG)

    def serialize_allowance(
        self, allowance: InvoiceAllowance, message_type: EInvoiceMessageType
    ) -> bytes:
        raise EInvoiceSerializerNotReady(self._MSG)
