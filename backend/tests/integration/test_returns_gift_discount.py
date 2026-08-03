"""折扣後退貨與贈品退回（P5）。

退款認**實付**（`net_amount`）並用差額法；贈品退回庫存但退款 0；主商品退了而贈品沒退，
店員必須明確說明原因。這幾條錯了就是真的多退或少退客人的錢。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.models import GiftReason, SaleLine
from app.modules.sales.pricing import DiscountRequest
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    AdjustmentScope,
    CalculationMethod,
    SaleLineKind,
    SaleLineType,
    TenderType,
    UserRole,
)
from app.shared.exceptions import ReturnConflict


async def _seed(session: AsyncSession) -> tuple[int, int, int, int]:
    """門市＋店長（開帳）＋商品＋贈品原因。回 (store_id, clerk_id, product_id, reason_id)。"""
    store = Store(name="退貨折扣店", tax_id="12345678")
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id, username=f"rg-{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    session.add(clerk)
    session.add(StoreSettings(store_id=store.id))
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("5000"))
    product = CatalogProduct(
        store_id=store.id,
        sku=f"RG-{store.id}",
        name="露營燈",
        unit_price=Decimal("500"),
        unit_cost=Decimal("200"),
        quantity_on_hand=50,
    )
    reason = GiftReason(store_id=store.id, code="PROMO", name="活動贈品")
    session.add_all([product, reason])
    await session.flush()
    return store.id, clerk.id, product.id, reason.id


def _line(product_id: int, qty: int = 1) -> SaleLineInput:
    return SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=qty)


def _gift(product_id: int, reason_id: int, qty: int = 1) -> SaleLineInput:
    return SaleLineInput(
        line_type=SaleLineType.CATALOG,
        catalog_product_id=product_id,
        qty=qty,
        line_kind=SaleLineKind.GIFT,
        gift_reason_id=reason_id,
    )


async def _lines_of(session: AsyncSession, sale_id: int) -> list[SaleLine]:
    return list(
        (
            await session.scalars(
                select(SaleLine).where(SaleLine.sale_id == sale_id).order_by(SaleLine.id)
            )
        ).all()
    )


# ── 折扣後退貨的金額 ────────────────────────────────────────────────────────


async def test_refund_is_based_on_what_was_actually_paid_not_the_listed_price(
    db_session: AsyncSession,
) -> None:
    """打了折的商品退貨只退實付。退牌價＝白送客人折扣金額。"""
    store_id, clerk_id, product_id, _reason = await _seed(db_session)
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(400))],
        adjustments=[
            DiscountRequest(
                AdjustmentScope.ITEM, CalculationMethod.FIXED_AMOUNT, Decimal(100), target_key="0"
            )
        ],
    )
    assert sale.total == Decimal(400)
    (line,) = await _lines_of(db_session, sale.id)

    customer_return = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(line.id, 1)],
        reason="不合用",
        actor_user_id=clerk_id,
        idempotency_key="rg-1",
    )
    assert customer_return.refund_amount == Decimal(400)  # 不是牌價 500


async def test_splitting_a_discounted_line_across_returns_never_drifts(
    db_session: AsyncSession,
) -> None:
    """500 折成 400、賣 3 件實付 1200；分三次退的加總必須恰好是 1200。

    每次各自四捨五入的話，加總會與原實付差幾元——少退坑客人、多退店家虧，且永遠對不平。
    """
    store_id, clerk_id, product_id, _reason = await _seed(db_session)
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id, qty=3)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(1400))],
        adjustments=[
            DiscountRequest(
                AdjustmentScope.ORDER, CalculationMethod.FIXED_AMOUNT, Decimal(100)
            )
        ],
    )
    assert sale.total == Decimal(1400)
    (line,) = await _lines_of(db_session, sale.id)

    returns = ReturnsService(db_session)
    refunds = []
    for index in range(3):
        result = await returns.create_return(
            store_id,
            sale_id=sale.id,
            lines=[ReturnLineInput(line.id, 1)],
            reason="分次退",
            actor_user_id=clerk_id,
            idempotency_key=f"rg-split-{index}",
        )
        refunds.append(result.refund_amount)
    assert sum(refunds) == Decimal(1400)


# ── 贈品退回 ────────────────────────────────────────────────────────────────


async def test_returning_a_gift_restocks_it_but_refunds_nothing(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[_gift(product_id, reason_id, qty=2)],
    )
    assert sale.total == Decimal(0)
    (gift_line,) = await _lines_of(db_session, sale.id)
    before = (await db_session.get(CatalogProduct, product_id)).quantity_on_hand  # type: ignore[union-attr]

    customer_return = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(gift_line.id, 1)],
        reason="客人不要",
        actor_user_id=clerk_id,
        idempotency_key="rg-gift-1",
    )

    assert customer_return.refund_amount == Decimal(0)
    assert customer_return.refund_tenders == []  # 零元退貨不產生退款渠道
    after = (await db_session.get(CatalogProduct, product_id)).quantity_on_hand  # type: ignore[union-attr]
    assert after == before + 1


async def test_gift_return_writes_a_distinguishable_stock_movement(
    db_session: AsyncSession,
) -> None:
    """贈品退回要與一般退貨分辨，否則報表算不出「送出去又退回來」幾件。"""
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id), _gift(product_id, reason_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(500))],
    )
    normal_line, gift_line = await _lines_of(db_session, sale.id)

    customer_return = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(normal_line.id, 1), ReturnLineInput(gift_line.id, 1)],
        reason="整組退回",
        actor_user_id=clerk_id,
        idempotency_key="rg-gift-2",
    )
    moves = (
        await db_session.scalars(
            select(StockMovement)
            .where(
                StockMovement.ref_type == "return",
                StockMovement.ref_id == customer_return.id,
            )
            .order_by(StockMovement.id)
        )
    ).all()
    assert sorted(m.reason.value for m in moves) == ["GIFT_RETURN", "RETURN"]
    assert customer_return.refund_amount == Decimal(500)  # 贈品不加錢


# ── 贈品未退的明確決定 ──────────────────────────────────────────────────────


async def test_returning_the_goods_without_the_gift_requires_an_explicit_reason(
    db_session: AsyncSession,
) -> None:
    """系統不自行假設贈品該不該收回——沒說明就擋下。"""
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id), _gift(product_id, reason_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(500))],
    )
    normal_line, _gift_line = await _lines_of(db_session, sale.id)

    with pytest.raises(ReturnConflict, match="贈品"):
        await ReturnsService(db_session).create_return(
            store_id,
            sale_id=sale.id,
            lines=[ReturnLineInput(normal_line.id, 1)],
            reason="只退主商品",
            actor_user_id=clerk_id,
            idempotency_key="rg-gift-3",
        )


async def test_an_explained_unreturned_gift_is_allowed_and_audited(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id), _gift(product_id, reason_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(500))],
    )
    normal_line, gift_line = await _lines_of(db_session, sale.id)

    customer_return = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(normal_line.id, 1)],
        reason="只退主商品",
        actor_user_id=clerk_id,
        idempotency_key="rg-gift-4",
        unreturned_gift_note="贈品已拆封無法回售，經客人同意不收回",
    )
    assert customer_return.refund_amount == Decimal(500)

    log = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "CREATE_RETURN",
            AuditLog.entity_id == str(customer_return.id),
        )
    )
    assert log is not None and log.after is not None
    assert log.after["unreturned_gift_note"] == "贈品已拆封無法回售，經客人同意不收回"
    assert log.after["unreturned_gifts"] == [
        {"sale_line_id": gift_line.id, "description": "露營燈", "qty": 1}
    ]


async def test_returning_only_the_gift_needs_no_explanation(
    db_session: AsyncSession,
) -> None:
    """只退贈品（主商品留著）不是「贈品沒收回」的情形，不該被擋。"""
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id), _gift(product_id, reason_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(500))],
    )
    _normal_line, gift_line = await _lines_of(db_session, sale.id)

    customer_return = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(gift_line.id, 1)],
        reason="贈品瑕疵收回",
        actor_user_id=clerk_id,
        idempotency_key="rg-gift-5",
    )
    assert customer_return.refund_amount == Decimal(0)


async def test_preview_reports_the_refund_and_the_gifts_still_out_there(
    db_session: AsyncSession,
) -> None:
    """畫面不必自己算退款金額，也要看得到還沒收回的贈品。"""
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id), _gift(product_id, reason_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(500))],
    )
    normal_line, gift_line = await _lines_of(db_session, sale.id)

    preview = await ReturnsService(db_session).preview_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(normal_line.id, 1)],
    )
    assert preview["refund_total"] == Decimal(500)
    assert preview["unreturned_gifts"] == [
        {
            "sale_line_id": gift_line.id,
            "description": "露營燈",
            "qty": 1,
            "retail_value": Decimal(500),
        }
    ]


# ── Codex 對抗審查（2026-08-03）的回歸 ──────────────────────────────────────


async def test_returning_a_discounted_item_does_not_reverse_more_revenue_than_it_earned(
    db_session: AsyncSession,
) -> None:
    """折後實收 400 的商品退貨，報表必須扣回 400，不是牌價 500。

    正向營收已改認 net_amount；反轉若仍用 unit_price × qty，會 +400 再 −500，
    整段期間顯示 −100 元營收（憑空生出的虧損）。
    """
    from app.modules.reports.service import ReportsService

    store_id, clerk_id, product_id, _reason = await _seed(db_session)
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(400))],
        adjustments=[
            DiscountRequest(
                AdjustmentScope.ITEM, CalculationMethod.FIXED_AMOUNT, Decimal(100), target_key="0"
            )
        ],
    )
    (line,) = await _lines_of(db_session, sale.id)
    await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(line.id, 1)],
        reason="不合用",
        actor_user_id=clerk_id,
        idempotency_key="rg-margin-1",
    )

    now = datetime.now(UTC)
    report = await ReportsService(db_session).sales_margin(
        store_id, date_from=now - timedelta(days=1), date_to=now + timedelta(days=1)
    )
    # 賣 400、退 400 → 這段期間淨營收為 0，不得變成負數
    assert report.gross_turnover == Decimal(0)
    assert report.recognized_revenue == Decimal(0)


async def test_returning_only_the_gift_of_an_invoiced_sale_succeeds(
    db_session: AsyncSession,
) -> None:
    """已開立發票的混合單只退贈品：退款 0，不得嘗試開零元折讓（折讓的 total 必須 > 0）。"""
    from app.modules.einvoice.service import EInvoiceService
    from app.shared.enums import InvoiceStatus

    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    settings = await db_session.scalar(
        select(StoreSettings).where(StoreSettings.store_id == store_id)
    )
    assert settings is not None
    settings.einvoice_enabled = True
    await db_session.flush()

    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id), _gift(product_id, reason_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(500))],
        expected_einvoice_enabled=True,
    )
    einvoice = EInvoiceService(db_session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    invoice.status = InvoiceStatus.ISSUED
    invoice.invoice_no = "ZZ00000001"
    await db_session.flush()

    _normal_line, gift_line = await _lines_of(db_session, sale.id)
    customer_return = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(gift_line.id, 1)],
        reason="贈品瑕疵收回",
        actor_user_id=clerk_id,
        idempotency_key="rg-gift-invoiced",
    )
    assert customer_return.refund_amount == Decimal(0)
    # 沒有折讓單被建立（零元折讓會違反 DB CHECK 而整筆回滾）
    assert await einvoice.get_allowance_for_return(store_id, customer_return.id) is None


async def test_preview_of_a_gift_only_return_asks_for_no_signature(
    db_session: AsyncSession,
) -> None:
    """退款 0 不涉及發票處置，畫面就不該要求收回紙本或請客人簽名。"""
    from app.modules.einvoice.service import EInvoiceService
    from app.shared.enums import InvoiceStatus

    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    settings = await db_session.scalar(
        select(StoreSettings).where(StoreSettings.store_id == store_id)
    )
    assert settings is not None
    settings.einvoice_enabled = True
    await db_session.flush()

    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id), _gift(product_id, reason_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(500))],
        expected_einvoice_enabled=True,
    )
    invoice = await EInvoiceService(db_session).get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    invoice.status = InvoiceStatus.ISSUED
    invoice.invoice_no = "ZZ00000002"
    await db_session.flush()

    _normal_line, gift_line = await _lines_of(db_session, sale.id)
    preview = await ReturnsService(db_session).preview_return(
        store_id, sale_id=sale.id, lines=[ReturnLineInput(gift_line.id, 1)]
    )
    assert preview["refund_total"] == Decimal(0)
    assert preview["invoice_action"] == "NONE"
    assert preview["requires_customer_consent"] is False
    assert preview["requires_paper_recall"] is False


async def test_returning_a_gift_does_not_invent_margin_in_the_report(
    db_session: AsyncSession,
) -> None:
    """贈品退回不得反轉一般商品成本（Codex 第三輪 high）。

    贈品成本在正向報表就獨立於毛利之外；反轉時若算進 catalog_cogs，退回一件成本 200 的
    贈品會讓當期 COGS 變成 −200，憑空生出 200 元毛利。
    """
    from app.modules.reports.service import ReportsService

    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[_gift(product_id, reason_id)],
    )
    (gift_line,) = await _lines_of(db_session, sale.id)
    await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(gift_line.id, 1)],
        reason="客人不要",
        actor_user_id=clerk_id,
        idempotency_key="rg-gift-margin",
    )

    now = datetime.now(UTC)
    report = await ReportsService(db_session).sales_margin(
        store_id, date_from=now - timedelta(days=1), date_to=now + timedelta(days=1)
    )
    # 全程沒有一般商品銷售：成本與毛利都必須是 0，不得因為退了贈品而變成負成本／正毛利
    assert report.catalog_cogs == Decimal(0)
    assert report.gross_margin == Decimal(0)


async def test_trends_and_daily_summary_include_catalog_cost(
    db_session: AsyncSession,
) -> None:
    """趨勢與日報的 COGS 必須含一般商品成本，否則同一份報表裡成本與毛利互相矛盾。"""
    from app.modules.reports.service import ReportsService

    store_id, clerk_id, product_id, _reason = await _seed(db_session)
    await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[_line(product_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(500))],
    )
    now = datetime.now(UTC)
    summary = await ReportsService(db_session).daily_summary(store_id, now.date())
    # 商品成本 200（_seed 的 unit_cost）→ 日報的 COGS 必須認列，毛利 = 500 − 200
    assert summary.cogs == Decimal(200)
    assert summary.gross_margin == Decimal(300)
