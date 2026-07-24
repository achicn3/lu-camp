"""簽署證據 canonical JSON 的穩定性守衛。"""

from decimal import Decimal

import pytest

from app.core.canonical import canonical_json_bytes
from app.modules.sales.inputs import InvoiceInfoInput, SaleLineInput, TenderInput
from app.modules.sales.service import _cart_fingerprint
from app.shared.enums import SaleLineType, TenderType


def test_canonical_json_normalizes_keys_unicode_and_decimal_without_exponent() -> None:
    first = {
        "金額": Decimal("1E+5"),
        "name": "e\u0301",
        "items": [{"qty": 1, "amount": Decimal("300.00")}],
    }
    second = {
        "items": [{"amount": "300.00", "qty": 1}],
        "name": "é",
        "金額": "100000",
    }

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert b"1E+5" not in canonical_json_bytes(first)


def test_canonical_json_rejects_float_money_values() -> None:
    with pytest.raises(ValueError, match="不接受浮點數"):
        canonical_json_bytes({"amount": 0.1})


def test_sale_fingerprint_normalizes_decimal_exponent_and_unicode() -> None:
    decomposed = _cart_fingerprint(
        [
            SaleLineInput(
                line_type=SaleLineType.SERIALIZED,
                item_code="E\u0301-001",
            )
        ],
        buyer_contact_id=7,
        tenders=[TenderInput(tender_type=TenderType.LINE_PAY, amount=Decimal("1E+3"))],
        invoice_info=InvoiceInfoInput(buyer_name="露營 E\u0301"),
    )
    composed = _cart_fingerprint(
        [
            SaleLineInput(
                line_type=SaleLineType.SERIALIZED,
                item_code="É-001",
            )
        ],
        buyer_contact_id=7,
        tenders=[TenderInput(tender_type=TenderType.LINE_PAY, amount=Decimal("1000"))],
        invoice_info=InvoiceInfoInput(buyer_name="露營 É"),
    )

    assert decomposed == composed
