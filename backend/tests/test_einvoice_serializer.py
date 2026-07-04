"""序列化器 seam 單元測試：預設 DeferredXmlSerializer 一律明確拋 NotReady。

守住「不憑記憶硬寫 MIG XML」的界線（CLAUDE.md §6、docs/14 §4）：在拿到官方 XSD 前，
任何序列化嘗試都應失敗而非產出可能錯誤的 XML。
"""

import pytest

from app.modules.einvoice.serializer import DeferredXmlSerializer, InvoiceXmlSerializer
from app.shared.enums import EInvoiceMessageType
from app.shared.exceptions import EInvoiceSerializerNotReady


def test_deferred_serializer_satisfies_protocol() -> None:
    assert isinstance(DeferredXmlSerializer(), InvoiceXmlSerializer)


def test_serialize_invoice_raises_not_ready() -> None:
    with pytest.raises(EInvoiceSerializerNotReady):
        DeferredXmlSerializer().serialize_invoice(None, EInvoiceMessageType.F0401)  # type: ignore[arg-type]


def test_serialize_allowance_raises_not_ready() -> None:
    with pytest.raises(EInvoiceSerializerNotReady):
        DeferredXmlSerializer().serialize_allowance(None, EInvoiceMessageType.G0401)  # type: ignore[arg-type]
