"""退貨發票處置政策的純邏輯測試（無 DB，快速覆蓋月界線／時區／折讓歷史等組合）。"""

from datetime import UTC, datetime

import pytest

from app.modules.returns.invoice_policy import (
    InvoiceFacts,
    ReturnInvoiceAction,
    decide,
    has_paper_copy,
    same_taipei_month,
)


def _facts(**overrides: object) -> InvoiceFacts:
    base: dict[str, object] = {
        "exists": True,
        "is_issued": True,
        "issued_at": datetime(2026, 8, 1, 2, 0, tzinfo=UTC),  # 台北 8/1 10:00
        "has_settled_allowance": False,
        "has_open_allowance": False,
        "has_inflight_void": False,
        "print_mark": True,
        "carrier_type": None,
        "donate_mark": False,
        "is_manual_paper": False,
    }
    base.update(overrides)
    return InvoiceFacts(**base)  # type: ignore[arg-type]


_NOW = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)  # 台北 8/10 11:00（與上方同月）


def test_no_invoice_means_no_tax_action() -> None:
    d = decide(_facts(exists=False), is_full_return=True, now=_NOW)
    assert d.action is ReturnInvoiceAction.NONE
    assert d.requires_customer_consent is False


def test_invoice_not_yet_issued_means_no_tax_action() -> None:
    d = decide(_facts(is_issued=False), is_full_return=True, now=_NOW)
    assert d.action is ReturnInvoiceAction.NONE


def test_full_return_same_month_voids_invoice() -> None:
    d = decide(_facts(), is_full_return=True, now=_NOW)
    assert d.action is ReturnInvoiceAction.VOID
    assert d.requires_paper_recall is True  # 有紙本
    assert d.requires_customer_consent is True


def test_partial_return_always_allowance() -> None:
    d = decide(_facts(), is_full_return=False, now=_NOW)
    assert d.action is ReturnInvoiceAction.ALLOWANCE
    # 部分退貨不得要求收回紙本：原發票對未退商品仍是客人的憑證
    assert d.requires_paper_recall is False
    assert d.requires_customer_consent is True


def test_full_return_cross_month_falls_back_to_allowance() -> None:
    d = decide(
        _facts(issued_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC)),
        is_full_return=True,
        now=_NOW,
    )
    assert d.action is ReturnInvoiceAction.ALLOWANCE


def test_existing_settled_allowance_blocks_void_forever() -> None:
    """先部分退（已折讓），之後把剩餘全退完 → 仍須折讓，不得作廢原發票。

    否則會同時存在「已作廢的原發票」與先前的折讓單，帳目自相矛盾。
    """
    d = decide(_facts(has_settled_allowance=True), is_full_return=True, now=_NOW)
    assert d.action is ReturnInvoiceAction.ALLOWANCE
    assert "已開過折讓" in d.reason


def test_inflight_void_blocks_everything() -> None:
    """作廢在途 → 任何稅務動作都不得疊加。"""
    d = decide(_facts(has_inflight_void=True), is_full_return=True, now=_NOW)
    assert d.action is ReturnInvoiceAction.REVIEW_REQUIRED


def test_open_allowance_keeps_using_allowance_never_voids() -> None:
    """折讓在途（尚未回執）→ 仍走折讓、絕不作廢；**不得因此擋下退款**。

    分次退貨會有多張 G0401 同時在途是正常操作，系統已能正確收斂。若在此轉人工，等於
    因為前一張折讓還沒回執就拒絕客人退款。
    """
    full = decide(_facts(has_open_allowance=True), is_full_return=True, now=_NOW)
    assert full.action is ReturnInvoiceAction.ALLOWANCE
    partial = decide(_facts(has_open_allowance=True), is_full_return=False, now=_NOW)
    assert partial.action is ReturnInvoiceAction.ALLOWANCE


def test_settled_allowance_wins_over_open_allowance() -> None:
    """已有成功折讓 → 本來就走折讓，不受在途折讓影響（都不會作廢）。"""
    d = decide(
        _facts(has_settled_allowance=True, has_open_allowance=True),
        is_full_return=True,
        now=_NOW,
    )
    assert d.action is ReturnInvoiceAction.ALLOWANCE


def test_carrier_invoice_needs_no_paper_recall() -> None:
    d = decide(_facts(carrier_type="3J0002"), is_full_return=True, now=_NOW)
    assert d.action is ReturnInvoiceAction.VOID
    assert d.requires_paper_recall is False
    assert d.requires_customer_consent is True  # 仍需同意


def test_donated_invoice_needs_no_paper_recall() -> None:
    d = decide(_facts(donate_mark=True), is_full_return=True, now=_NOW)
    assert d.action is ReturnInvoiceAction.VOID
    assert d.requires_paper_recall is False
    assert d.requires_customer_consent is True


def test_unprinted_invoice_needs_no_paper_recall() -> None:
    d = decide(_facts(print_mark=False), is_full_return=True, now=_NOW)
    assert d.requires_paper_recall is False


@pytest.mark.parametrize(
    ("issued_utc", "now_utc", "expected_same"),
    [
        # 台北 1/31 23:00 開立、台北 2/1 00:30 退貨 → 跨月
        (
            datetime(2026, 1, 31, 15, 0, tzinfo=UTC),
            datetime(2026, 1, 31, 16, 30, tzinfo=UTC),
            False,
        ),
        # UTC 1/31 16:00＝台北 2/1 00:00：以**台北**月份判定，與 2/5 同月
        (datetime(2026, 1, 31, 16, 0, tzinfo=UTC), datetime(2026, 2, 5, 3, 0, tzinfo=UTC), True),
        # 同為台北 8 月
        (datetime(2026, 8, 1, 2, 0, tzinfo=UTC), datetime(2026, 8, 31, 15, 0, tzinfo=UTC), True),
        # 台北 8/31 23:59 vs 9/1 00:01 → 跨月
        (
            datetime(2026, 8, 31, 15, 59, tzinfo=UTC),
            datetime(2026, 8, 31, 16, 1, tzinfo=UTC),
            False,
        ),
    ],
)
def test_same_taipei_month_boundaries(
    issued_utc: datetime, now_utc: datetime, expected_same: bool
) -> None:
    assert same_taipei_month(issued_utc, now_utc) is expected_same


def test_month_boundary_drives_void_vs_allowance() -> None:
    """月界線直接決定作廢或折讓（以台北時區為準）。"""
    issued = datetime(2026, 1, 31, 15, 0, tzinfo=UTC)  # 台北 1/31 23:00
    same_month = decide(
        _facts(issued_at=issued), is_full_return=True, now=datetime(2026, 1, 31, 15, 30, tzinfo=UTC)
    )
    assert same_month.action is ReturnInvoiceAction.VOID
    next_month = decide(
        _facts(issued_at=issued), is_full_return=True, now=datetime(2026, 1, 31, 16, 30, tzinfo=UTC)
    )
    assert next_month.action is ReturnInvoiceAction.ALLOWANCE


def test_has_paper_copy_matrix() -> None:
    assert has_paper_copy(_facts()) is True
    assert has_paper_copy(_facts(print_mark=False)) is False
    assert has_paper_copy(_facts(carrier_type="3J0002")) is False
    assert has_paper_copy(_facts(donate_mark=True)) is False


# ── 手開紙本發票（docs/36） ──


@pytest.mark.parametrize("is_full_return", [True, False])
def test_manual_paper_invoice_always_goes_to_manual_review(is_full_return: bool) -> None:
    """手開紙本發票的退貨一律轉人工：作廢與折讓都得走國稅局的紙本程序，系統不代管。

    無論全退或部分退——自動走 F0501/G0401 會對著一張**平台上根本不存在**的發票送稅務訊息。
    """
    d = decide(_facts(is_manual_paper=True), is_full_return=is_full_return, now=_NOW)
    assert d.action is ReturnInvoiceAction.REVIEW_REQUIRED
    assert d.requires_customer_consent is True
    assert "紙本" in d.reason


def test_manual_paper_takes_priority_over_existing_allowance() -> None:
    """已有折讓也不改判：紙本發票的折讓不可能是系統開的，仍須人工。"""
    d = decide(
        _facts(is_manual_paper=True, has_settled_allowance=True),
        is_full_return=False,
        now=_NOW,
    )
    assert d.action is ReturnInvoiceAction.REVIEW_REQUIRED


def test_amego_invoice_is_unaffected_by_the_new_fact() -> None:
    """預設（電子發票）行為完全不變。"""
    d = decide(_facts(), is_full_return=False, now=_NOW)
    assert d.action is ReturnInvoiceAction.ALLOWANCE
