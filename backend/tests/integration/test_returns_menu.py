"""餐點退款（docs/47 E1）：退貨引擎接受餐點行，金額、退款去向、點數、份數都要對。

混合單：二手 1000 ＋ 拿鐵 150 × 2，付款 購物金 400 ＋ 現金 900（總額 1300），會員點數 10
（只發非餐飲部分 floor(1000/100)）。

- 購物金先算在二手上（店主 2026-10-09 起餐點也能用購物金）：購物金沒超過二手小計時，
  餐點等於全用外部付款 → 退餐點**只退外部付款**（現金），不會把現金退成購物金。
- 購物金超過二手小計的那部分是付在餐點上，退餐點時才退回購物金。
- 二手照舊購物金優先；兩邊累計加總恆等於原付款。
- 點數只按二手退款沖回，退餐點不沖。
- 「這份還能賣」→ 份數依同一營業日、同一版本加回；不勾不動。
"""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.models import CashMovement
from app.modules.contacts.models import Contact
from app.modules.einvoice.models import InvoiceAllowance
from app.modules.menu.service import MenuService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.models import Sale, SaleLine
from app.modules.sales.service import SalesService
from app.modules.storecredit.service import StoreCreditService
from app.shared.enums import (
    InvoiceAllowanceSource,
    SaleLineType,
    SaleStatus,
    ServiceMode,
    TenderType,
)
from tests.integration.customer_display_helpers import prepare_signed_store_credit_cart
from tests.integration.test_returns_invoice_tenders import (
    _consent,
    _issue,
    _item,
    _seed,
    _tenders,
)


class _Mixed:
    def __init__(self) -> None:
        self.store_id = 0
        self.clerk_id = 0
        self.member_id = 0
        self.sale_id = 0
        self.item_line = 0
        self.latte_line = 0
        self.latte_id = 0


async def _mixed_sale(session: AsyncSession, *, daily_limited: bool = False) -> _Mixed:
    store_id, clerk_id, member_id = await _seed(session)
    code = await _item(session, store_id, f"MX-{store_id}", "1000")
    menu = MenuService(session)
    latte = await menu.create_menu_item(
        store_id, name="拿鐵", unit_price=Decimal(150), actor_user_id=clerk_id
    )
    if daily_limited:
        await menu.update_menu_item(store_id, latte.id, daily_limited=True, actor_user_id=clerk_id)
        await menu.set_daily_stock(
            store_id, "item", latte.id, qty=5, expected_remaining=0, actor_user_id=clerk_id
        )
    await StoreCreditService(session).adjust(
        store_id,
        member_id,
        amount=Decimal("400"),
        reason="測試入帳",
        created_by=clerk_id,
        idempotency_key=f"mx-seed-{store_id}",
    )
    signed = await prepare_signed_store_credit_cart(
        session,
        store_id=store_id,
        actor_user_id=clerk_id,
        payload={
            "buyer_contact_id": member_id,
            "lines": [
                {"line_type": "SERIALIZED", "item_code": code, "qty": 1},
                {"line_type": "MENU", "menu_item_id": latte.id, "qty": 2},
            ],
            "tenders": [
                {"tender_type": "STORE_CREDIT", "amount": "400"},
                {"tender_type": "CASH", "amount": "900"},
            ],
            "service_mode": "TAKEOUT",
        },
    )
    sale = await SalesService(session).create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code),
            SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=latte.id, qty=2),
        ],
        buyer_contact_id=member_id,
        tenders=[
            TenderInput(tender_type=TenderType.STORE_CREDIT, amount=Decimal("400")),
            TenderInput(tender_type=TenderType.CASH, amount=Decimal("900")),
        ],
        idempotency_key=f"mx-sale-{store_id}",
        service_mode=ServiceMode.TAKEOUT,
        signature_task_id=signed.signature_task_id,
        cart_session_id=signed.cart_session_id,
        cart_revision=signed.cart_revision,
    )
    lines = await SalesService(session).get_lines(sale.id)
    m = _Mixed()
    m.store_id, m.clerk_id, m.member_id, m.sale_id = store_id, clerk_id, member_id, sale.id
    m.item_line = next(ln.id for ln in lines if ln.line_type is SaleLineType.SERIALIZED)
    m.latte_line = next(ln.id for ln in lines if ln.line_type is SaleLineType.MENU)
    m.latte_id = latte.id
    return m


async def _points(session: AsyncSession, member_id: int) -> int:
    member = await session.get(Contact, member_id)
    assert member is not None
    await session.refresh(member)
    return int(member.member_points)


async def _return(
    session: AsyncSession,
    m: _Mixed,
    lines: list[ReturnLineInput],
    key: str,
    *,
    consent: int | None = None,
) -> object:
    return await ReturnsService(session).create_return(
        m.store_id,
        sale_id=m.sale_id,
        lines=lines,
        reason="客人不滿意",
        actor_user_id=m.clerk_id,
        idempotency_key=key,
        consent_signature_task_id=consent,
    )


async def test_refunding_food_goes_back_to_cash_not_store_credit(
    db_session: AsyncSession,
) -> None:
    m = await _mixed_sale(db_session)
    assert await _points(db_session, m.member_id) == 10
    credit = StoreCreditService(db_session)
    assert await credit.get_balance(m.store_id, m.member_id) == Decimal("0")

    ret = await _return(db_session, m, [ReturnLineInput(m.latte_line, 1)], "r-food-1")

    assert _tenders(ret) == [(TenderType.CASH, Decimal("150"))]
    assert await credit.get_balance(m.store_id, m.member_id) == Decimal("0")
    cash_out = await db_session.scalar(
        select(CashMovement).where(CashMovement.ref_type == "return", CashMovement.ref_id == ret.id)  # type: ignore[attr-defined]
    )
    assert cash_out is not None and cash_out.amount == Decimal("150")
    assert await _points(db_session, m.member_id) == 10  # 餐點沒發點數，退餐點不沖


async def test_food_then_secondhand_split_adds_up_to_what_was_paid(
    db_session: AsyncSession,
) -> None:
    """先退一杯、再退二手、最後退另一杯：購物金只退 400、現金退 900，總和＝原付款 1300。"""
    m = await _mixed_sale(db_session)
    r1 = await _return(db_session, m, [ReturnLineInput(m.latte_line, 1)], "r-a")
    r2 = await _return(db_session, m, [ReturnLineInput(m.item_line, 1)], "r-b")
    r3 = await _return(db_session, m, [ReturnLineInput(m.latte_line, 1)], "r-c")

    assert _tenders(r1) == [(TenderType.CASH, Decimal("150"))]
    assert _tenders(r2) == [
        (TenderType.CASH, Decimal("600")),
        (TenderType.STORE_CREDIT, Decimal("400")),
    ]
    assert _tenders(r3) == [(TenderType.CASH, Decimal("150"))]
    assert await _points(db_session, m.member_id) == 0  # 二手退光才沖光
    sale = await db_session.get(Sale, m.sale_id)
    assert sale is not None and sale.status is SaleStatus.RETURNED


async def test_secondhand_first_still_keeps_food_refund_on_cash(
    db_session: AsyncSession,
) -> None:
    m = await _mixed_sale(db_session)
    r1 = await _return(db_session, m, [ReturnLineInput(m.item_line, 1)], "s-a")
    r2 = await _return(db_session, m, [ReturnLineInput(m.latte_line, 2)], "s-b")
    assert _tenders(r1) == [
        (TenderType.CASH, Decimal("600")),
        (TenderType.STORE_CREDIT, Decimal("400")),
    ]
    assert _tenders(r2) == [(TenderType.CASH, Decimal("300"))]


async def test_food_and_secondhand_in_one_return(db_session: AsyncSession) -> None:
    m = await _mixed_sale(db_session)
    ret = await _return(
        db_session,
        m,
        [ReturnLineInput(m.item_line, 1), ReturnLineInput(m.latte_line, 1)],
        "both",
    )
    assert _tenders(ret) == [
        (TenderType.CASH, Decimal("750")),
        (TenderType.STORE_CREDIT, Decimal("400")),
    ]


async def test_resellable_puts_daily_stock_back(db_session: AsyncSession) -> None:
    m = await _mixed_sale(db_session, daily_limited=True)
    menu = MenuService(db_session)
    assert await menu.remaining(m.store_id, "item", m.latte_id) == 3
    await _return(db_session, m, [ReturnLineInput(m.latte_line, 1)], "keep")
    assert await menu.remaining(m.store_id, "item", m.latte_id) == 3  # 預設不加回
    await _return(db_session, m, [ReturnLineInput(m.latte_line, 1, resellable=True)], "resell")
    assert await menu.remaining(m.store_id, "item", m.latte_id) == 4


async def test_issued_invoice_food_refund_opens_allowance(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    m = await _mixed_sale(db_session)
    invoice = await _issue(db_session, m.store_id, m.sale_id, tmp_path)
    consent = await _consent(
        db_session,
        m.store_id,
        m.sale_id,
        contact_id=m.member_id,
        created_by=m.clerk_id,
        return_lines={m.latte_line: 1},
    )
    ret = await _return(db_session, m, [ReturnLineInput(m.latte_line, 1)], "inv", consent=consent)
    allowance = await db_session.scalar(
        select(InvoiceAllowance).where(
            InvoiceAllowance.invoice_id == invoice.id,
            InvoiceAllowance.source == InvoiceAllowanceSource.RETURN,
        )
    )
    assert allowance is not None
    assert allowance.total == Decimal("150")
    assert _tenders(ret) == [(TenderType.CASH, Decimal("150"))]


async def test_preview_prices_food_lines(db_session: AsyncSession) -> None:
    m = await _mixed_sale(db_session)
    preview = await ReturnsService(db_session).preview_return(
        m.store_id, sale_id=m.sale_id, lines=[ReturnLineInput(m.latte_line, 2)]
    )
    assert preview["refund_total"] == Decimal("300")
    assert preview["is_full_return"] is False


async def test_menu_line_on_sale_line_model_is_unchanged(db_session: AsyncSession) -> None:
    """退款不改寫原銷售明細（歷史不動）。"""
    m = await _mixed_sale(db_session)
    await _return(db_session, m, [ReturnLineInput(m.latte_line, 1)], "hist")
    line = await db_session.get(SaleLine, m.latte_line)
    assert line is not None and line.qty == 2 and line.net_amount == Decimal("300")


async def test_margin_report_nets_food_refunds(db_session: AsyncSession) -> None:
    """退一杯拿鐵（150）且勾還能賣：餐飲營收扣 150；成本照退量比例扣回，毛利同步反轉。"""
    from datetime import UTC, datetime, timedelta

    m = await _mixed_sale(db_session)
    line = await db_session.get(SaleLine, m.latte_line)
    assert line is not None
    line.cost_snapshot = Decimal(90)  # 兩杯成本 90（每杯 45）
    await db_session.flush()
    t0, t1 = datetime.now(UTC) - timedelta(hours=1), datetime.now(UTC) + timedelta(hours=1)
    sales = SalesService(db_session)
    before = await sales.margin_breakdown(m.store_id, t0, t1)
    # 勾「還能賣」＝這份沒報銷：營收與成本一起扣回（沒勾的見 test_food_waste.py，成本改算損耗）。
    await _return(db_session, m, [ReturnLineInput(m.latte_line, 1, resellable=True)], "rep")
    after = await sales.margin_breakdown(m.store_id, t0, t1)

    assert before.food_revenue - after.food_revenue == Decimal(150)
    assert before.food_cogs - after.food_cogs == Decimal(45)
    assert before.food_margin - after.food_margin == Decimal(105)
    assert before.secondhand_revenue == after.secondhand_revenue
    assert before.recognized_revenue - after.recognized_revenue == Decimal(150)


async def test_margin_report_nets_food_refund_without_cost(db_session: AsyncSession) -> None:
    """沒填成本的餐點：營收在「成本未知」桶，退款也從那裡扣，不動毛利。"""
    from datetime import UTC, datetime, timedelta

    m = await _mixed_sale(db_session)
    t0, t1 = datetime.now(UTC) - timedelta(hours=1), datetime.now(UTC) + timedelta(hours=1)
    sales = SalesService(db_session)
    before = await sales.margin_breakdown(m.store_id, t0, t1)
    await _return(db_session, m, [ReturnLineInput(m.latte_line, 2)], "rep2")
    after = await sales.margin_breakdown(m.store_id, t0, t1)

    assert before.food_revenue - after.food_revenue == Decimal(300)
    assert before.unknown_cost_sales - after.unknown_cost_sales == Decimal(300)
    assert before.gross_margin == after.gross_margin


async def test_dine_in_report_nets_food_refunds(db_session: AsyncSession) -> None:
    """內用／外帶報表（docs/39）：每組的餐飲營收扣掉已退的餐點；全退光的那組不算一組。"""
    from datetime import UTC, datetime, timedelta

    from app.modules.reports.service import ReportsService

    m = await _mixed_sale(db_session)
    t0, t1 = datetime.now(UTC) - timedelta(hours=1), datetime.now(UTC) + timedelta(hours=1)
    reports = ReportsService(db_session)

    async def takeout() -> tuple[int, Decimal, Decimal]:
        report = await reports.dine_in_report(
            m.store_id, date_from=t0, date_to=t1, granularity="day"
        )
        stats = report.summary.takeout
        return stats.groups, stats.fnb_revenue, stats.gross_total

    assert await takeout() == (1, Decimal(300), Decimal(1300))
    await _return(db_session, m, [ReturnLineInput(m.latte_line, 1)], "dine-1")
    assert await takeout() == (1, Decimal(150), Decimal(1150))
    await _return(db_session, m, [ReturnLineInput(m.latte_line, 1)], "dine-2")
    assert (await takeout())[0] == 0  # 餐點全退光：這組不算


async def test_refunds_by_sale_ignores_voided_sales(db_session: AsyncSession) -> None:
    """退款統計與報表母體同口徑：作廢單排除（實務上有退貨的單不能作廢，此為防線）。"""
    m = await _mixed_sale(db_session)
    await _return(db_session, m, [ReturnLineInput(m.latte_line, 1)], "vd")
    returns = ReturnsService(db_session)
    assert await returns.refunds_by_sale(m.store_id, [m.sale_id]) == {
        m.sale_id: (Decimal(150), Decimal(150))
    }
    sale = await db_session.get(Sale, m.sale_id)
    assert sale is not None
    sale.status = SaleStatus.VOIDED
    await db_session.flush()
    assert await returns.refunds_by_sale(m.store_id, [m.sale_id]) == {}


async def test_preview_tells_where_the_money_goes(db_session: AsyncSession) -> None:
    """退款去向由後端預覽給出（畫面不再自己算一份）：餐點→現金；二手→購物金優先。"""
    m = await _mixed_sale(db_session)
    svc = ReturnsService(db_session)
    food = await svc.preview_return(
        m.store_id, sale_id=m.sale_id, lines=[ReturnLineInput(m.latte_line, 1)]
    )
    assert food["refund_supported"] is True
    assert food["refund_tenders"] == [(TenderType.CASH, Decimal(150))]
    other = await svc.preview_return(
        m.store_id, sale_id=m.sale_id, lines=[ReturnLineInput(m.item_line, 1)]
    )
    assert other["refund_tenders"] == [
        (TenderType.STORE_CREDIT, Decimal(400)),
        (TenderType.CASH, Decimal(600)),
    ]


async def test_retry_with_changed_resellable_is_rejected_not_silently_replayed(
    db_session: AsyncSession,
) -> None:
    """回應遺失後改了「還能賣」再重試：不可默默回原單（份數會跟店員以為的不同），要明確擋下。"""
    import pytest

    from app.shared.exceptions import IdempotencyKeyConflict

    m = await _mixed_sale(db_session, daily_limited=True)
    first = await _return(
        db_session, m, [ReturnLineInput(m.latte_line, 1, resellable=True)], "same-key"
    )
    again = await _return(
        db_session, m, [ReturnLineInput(m.latte_line, 1, resellable=True)], "same-key"
    )
    assert again.id == first.id  # type: ignore[attr-defined]
    with pytest.raises(IdempotencyKeyConflict):
        await _return(db_session, m, [ReturnLineInput(m.latte_line, 1)], "same-key")


def test_fingerprint_without_resellable_keeps_old_shape() -> None:
    """沒勾還能賣：指紋與加欄位前完全相同（部署前送出、回應遺失的重送照樣認得）。"""
    import hashlib
    import json

    from app.modules.returns.service import _return_fingerprint

    old = hashlib.sha256(
        json.dumps(
            {"sale_id": 7, "reason": "r", "lines": [{"sale_line_id": 3, "qty": 1}]},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert _return_fingerprint(7, {3: 1}, "r") == old
    assert _return_fingerprint(7, {3: 1}, "r", frozenset({3})) != old


def test_refund_allocation_any_order_adds_up_exactly() -> None:
    """餐點／二手任意交錯分次退（Codex 對抗審查 E3 #1 的驗證）：每一步不超退任何付款渠道，
    退完時購物金恰好退回購物金付款、現金恰好退回現金付款
    （購物金 400＋現金 900；二手 1000、餐點 300）。"""
    import itertools

    from app.modules.sales.models import SaleTender
    from app.shared.enums import PaymentMethod

    tenders = [
        SaleTender(tender_type=TenderType.STORE_CREDIT, amount=Decimal(400)),
        SaleTender(tender_type=TenderType.CASH, amount=Decimal(900)),
    ]
    steps = [
        ("food", Decimal(100)),
        ("food", Decimal(200)),
        ("other", Decimal(250)),
        ("other", Decimal(750)),
    ]
    for order in itertools.permutations(steps):
        food = other = Decimal(0)
        paid: dict[TenderType, Decimal] = {
            TenderType.STORE_CREDIT: Decimal(0),
            TenderType.CASH: Decimal(0),
        }
        for kind, amount in order:
            legs = ReturnsService._refund_allocations(
                PaymentMethod.MIXED,
                tenders,
                previous_food=food,
                previous_other=other,
                other_total=Decimal(1000),
                refund_food=amount if kind == "food" else Decimal(0),
                refund_other=amount if kind == "other" else Decimal(0),
            )
            assert sum((v for _, v in legs), Decimal(0)) == amount
            for tender, value in legs:
                paid[tender] += value
            assert paid[TenderType.STORE_CREDIT] <= 400 and paid[TenderType.CASH] <= 900
            if kind == "food":
                food += amount
                assert all(t is TenderType.CASH for t, _ in legs)  # 餐點永不退成購物金
            else:
                other += amount
        assert paid == {TenderType.STORE_CREDIT: Decimal(400), TenderType.CASH: Decimal(900)}


def test_refund_allocation_food_paid_with_store_credit_adds_up_exactly() -> None:
    """餐點也用購物金付（店主 2026-10-09）：二手 100、餐點 300，付 購物金 350＋現金 50。
    購物金先算在二手（100），其餘 250 付在餐點上。任意交錯分次退：不超退任何渠道、
    二手退款只動到二手那份購物金、退完恰好等於原付款。"""
    import itertools

    from app.modules.sales.models import SaleTender
    from app.shared.enums import PaymentMethod

    tenders = [
        SaleTender(tender_type=TenderType.STORE_CREDIT, amount=Decimal(350)),
        SaleTender(tender_type=TenderType.CASH, amount=Decimal(50)),
    ]
    steps = [
        ("food", Decimal(120)),
        ("food", Decimal(180)),
        ("other", Decimal(40)),
        ("other", Decimal(60)),
    ]
    for order in itertools.permutations(steps):
        food = other = Decimal(0)
        paid: dict[TenderType, Decimal] = {
            TenderType.STORE_CREDIT: Decimal(0),
            TenderType.CASH: Decimal(0),
        }
        for kind, amount in order:
            legs = ReturnsService._refund_allocations(
                PaymentMethod.MIXED,
                tenders,
                previous_food=food,
                previous_other=other,
                other_total=Decimal(100),
                refund_food=amount if kind == "food" else Decimal(0),
                refund_other=amount if kind == "other" else Decimal(0),
            )
            assert sum((v for _, v in legs), Decimal(0)) == amount
            for tender, value in legs:
                paid[tender] += value
            assert paid[TenderType.STORE_CREDIT] <= 350 and paid[TenderType.CASH] <= 50
            if kind == "food":
                food += amount
            else:
                other += amount
                assert all(t is TenderType.STORE_CREDIT for t, _ in legs)  # 二手全由購物金付
        assert paid == {TenderType.STORE_CREDIT: Decimal(350), TenderType.CASH: Decimal(50)}


async def test_food_only_sale_paid_by_store_credit_refunds_to_store_credit(
    db_session: AsyncSession,
) -> None:
    """純餐點、全額購物金付：退貨退回購物金（以前會因「沒有可退回餐點款項的付款渠道」被擋）。"""
    store_id, clerk_id, member_id = await _seed(db_session)
    latte = await MenuService(db_session).create_menu_item(
        store_id, name="拿鐵", unit_price=Decimal(150), actor_user_id=clerk_id
    )
    credit = StoreCreditService(db_session)
    await credit.adjust(
        store_id,
        member_id,
        amount=Decimal("300"),
        reason="測試入帳",
        created_by=clerk_id,
        idempotency_key=f"fo-seed-{store_id}",
    )
    signed = await prepare_signed_store_credit_cart(
        db_session,
        store_id=store_id,
        actor_user_id=clerk_id,
        payload={
            "buyer_contact_id": member_id,
            "lines": [{"line_type": "MENU", "menu_item_id": latte.id, "qty": 2}],
            "tenders": [{"tender_type": "STORE_CREDIT", "amount": "300"}],
            "service_mode": "TAKEOUT",
        },
    )
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=latte.id, qty=2)],
        buyer_contact_id=member_id,
        tenders=[TenderInput(tender_type=TenderType.STORE_CREDIT, amount=Decimal("300"))],
        idempotency_key=f"fo-sale-{store_id}",
        service_mode=ServiceMode.TAKEOUT,
        signature_task_id=signed.signature_task_id,
        cart_session_id=signed.cart_session_id,
        cart_revision=signed.cart_revision,
    )
    assert await credit.get_balance(store_id, member_id) == Decimal("0")
    [line] = await SalesService(db_session).get_lines(sale.id)

    ret = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(line.id, 1)],
        reason="客人不滿意",
        actor_user_id=clerk_id,
        idempotency_key=f"fo-ret-{store_id}",
    )

    assert _tenders(ret) == [(TenderType.STORE_CREDIT, Decimal("150"))]
    assert await credit.get_balance(store_id, member_id) == Decimal("150")
