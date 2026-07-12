"""證明聯年期別標示（附件一格式一第 3 項）。條碼/QR 內容由 Amego 平台回傳，本機不產。"""

from datetime import date

from agent.drivers.einvoice_format import roc_period_label


def test_odd_month_maps_to_period_end() -> None:
    assert roc_period_label(date(2013, 5, 7)) == "102年05-06月"


def test_even_month_uses_own_period() -> None:
    assert roc_period_label(date(2026, 6, 10)) == "115年05-06月"


def test_january_period() -> None:
    assert roc_period_label(date(2026, 1, 3)) == "115年01-02月"
