"""排隊收購的收購明細也印寄售品（docs/42 §13；店主 2026-10-02）。

寄售品客人也簽了切結：明細上一樣要看得到名稱、寄售售價、抽成。寄售現在不付錢，不算進收購總額；
只賣寄售（合計 0）時沒有撥款方式，改印「寄售，賣出後分帳」。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.drivers.escpos_receipt import EscposReceiptPrinter
from agent.escpos_printer import FakePrinter
from agent.interfaces import AcquisitionReceiptConsignment
from tests.test_print_signature import _HEADER, _acq_receipt, _big5

_TENT = AcquisitionReceiptConsignment(name="帳篷", listed_price="6000", commission_pct=50)


def _print(**overrides: object) -> bytes:
    buf = FakePrinter()
    EscposReceiptPrinter(buf).print_acquisition(_acq_receipt(**overrides), _HEADER)
    return bytes(buf.buffer)


def test_mixed_receipt_lists_consignments_with_price_and_commission() -> None:
    data = _print(consignments=[_TENT])
    for text in ("登山外套", "寄售（賣出後分帳）", "帳篷", "售價 6000 抽成 50%", "收購總額 1200"):
        assert _big5(text) in data, text
    assert _big5("撥款方式：購物金") in data


def test_consignment_only_receipt_has_no_payout() -> None:
    data = _print(
        items=[],
        total="0",
        payout_method=None,
        store_credit_granted=None,
        store_credit_balance_after=None,
        consignments=[_TENT],
    )
    assert _big5("帳篷") in data
    assert _big5("撥款方式：寄售，賣出後分帳") in data
    assert _big5("收購總額") not in data
    assert _big5("現金") not in data


def test_missing_payout_only_allowed_for_consignment_only() -> None:
    """沒有撥款方式只限「合計 0 而且有寄售品」；有付錢的憑證仍一定要有撥款方式。"""
    no_payout = {
        "payout_method": None,
        "store_credit_granted": None,
        "store_credit_balance_after": None,
    }
    with pytest.raises(ValidationError):
        _acq_receipt(**no_payout)  # 合計 1200 卻沒有撥款方式
    with pytest.raises(ValidationError):
        _acq_receipt(items=[], total="0", **no_payout)  # 什麼都沒有


def test_consignment_line_rejects_bad_values() -> None:
    with pytest.raises(ValidationError):
        AcquisitionReceiptConsignment(name="帳篷", listed_price="6,000", commission_pct=50)
    with pytest.raises(ValidationError):
        AcquisitionReceiptConsignment(name="帳篷", listed_price="6000", commission_pct=101)
