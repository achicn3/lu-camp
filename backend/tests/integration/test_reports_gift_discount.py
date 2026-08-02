"""折扣與贈品的報表口徑（P6）。

三條紅線：
1. 營收認**實付**（net_amount）——用牌價會讓報表上的營收大於實際收到的錢。
2. 贈品成本**不進商品毛利**——營收 0 加全額成本會讓毛利率失真；它要單獨看得見。
3. 折扣金額取落盤的 `applied_amount`，**不事後重算**——商品價格與活動日後都會變。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct
from app.modules.inventory.service import InventoryService
from app.modules.reports.service import ReportsService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.models import DiscountReason, GiftReason
from app.modules.sales.pricing import DiscountRequest
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    AdjustmentScope,
    CalculationMethod,
    Grade,
    OwnershipType,
    SaleLineKind,
    SaleLineType,
    TenderType,
    UserRole,
)


async def _seed(session: AsyncSession) -> tuple[int, int, int, int, int]:
    """門市＋店長（開帳）＋商品＋贈品原因＋折扣原因。"""
    store = Store(name="報表店", tax_id="12345678")
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id, username=f"rp-{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    session.add(clerk)
    session.add(StoreSettings(store_id=store.id))
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("5000"))
    product = CatalogProduct(
        store_id=store.id,
        sku=f"RP-{store.id}",
        name="露營燈",
        unit_price=Decimal("500"),
        unit_cost=Decimal("200"),
        quantity_on_hand=50,
    )
    gift_reason = GiftReason(store_id=store.id, code="PROMOTION", name="活動贈品")
    discount_reason = DiscountReason(store_id=store.id, code="DEFECT", name="商品瑕疵")
    session.add_all([product, gift_reason, discount_reason])
    await session.flush()
    return store.id, clerk.id, product.id, gift_reason.id, discount_reason.id


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now - timedelta(days=1), now + timedelta(days=1)


async def test_revenue_counts_what_was_paid_not_the_listed_price(
    db_session: AsyncSession,
) -> None:
    """自有序號品打了折：報表營收必須是實付，否則帳面營收大於實際收到的錢。"""
    store_id, clerk_id, _product_id, _gift, _disc = await _seed(db_session)
    item = await InventoryService(db_session).create_serialized_item(
        store_id,
        item_code=f"RPS-{store_id}",
        name="二手帳篷",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("2000"),
        acquisition_cost=Decimal("800"),
    )
    await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=item.item_code)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(1700))],
        adjustments=[
            DiscountRequest(
                AdjustmentScope.ITEM, CalculationMethod.FIXED_AMOUNT, Decimal(300), target_key="0"
            )
        ],
    )
    date_from, date_to = _window()
    breakdown = await SalesService(db_session).margin_breakdown(store_id, date_from, date_to)

    assert breakdown.gross_turnover == Decimal(1700)  # 不是牌價 2000
    assert breakdown.owned_cogs == Decimal(800)
    assert breakdown.gross_margin == Decimal(900)  # 1700 − 800
    assert breakdown.manual_discount_total == Decimal(300)


async def test_gift_cost_is_reported_separately_and_never_drags_the_margin_down(
    db_session: AsyncSession,
) -> None:
    """贈品營收 0、成本 200：毛利不得因此變負，成本要單獨看得見。"""
    store_id, clerk_id, product_id, gift_reason, _disc = await _seed(db_session)
    await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(
                line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=1
            ),
            SaleLineInput(
                line_type=SaleLineType.CATALOG,
                catalog_product_id=product_id,
                qty=1,
                line_kind=SaleLineKind.GIFT,
                gift_reason_id=gift_reason,
            ),
        ],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(500))],
    )
    date_from, date_to = _window()
    breakdown = await SalesService(db_session).margin_breakdown(store_id, date_from, date_to)

    assert breakdown.gross_turnover == Decimal(500)  # 贈品不計入營業額
    assert breakdown.gift_retail_value == Decimal(500)
    assert breakdown.gift_cost == Decimal(200)
    assert breakdown.gross_margin >= 0  # 贈品成本沒有把毛利拖成負的
    assert breakdown.contribution_margin == breakdown.net_margin - Decimal(200)


async def test_discount_report_groups_by_reason_and_by_clerk(
    db_session: AsyncSession,
) -> None:
    """無主管核准機制，事後稽核靠這份——依店員那一段是重點。"""
    store_id, clerk_id, product_id, _gift, discount_reason = await _seed(db_session)
    sales = SalesService(db_session)
    await sales.create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=2)
        ],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(900))],
        adjustments=[
            DiscountRequest(
                AdjustmentScope.ORDER,
                CalculationMethod.FIXED_AMOUNT,
                Decimal(100),
                reason_id=discount_reason,
            )
        ],
    )
    date_from, date_to = _window()
    report = await ReportsService(db_session).discount_report(
        store_id, date_from=date_from, date_to=date_to
    )

    assert report.discount_total == Decimal(100)
    assert report.order_discount_total == Decimal(100)
    assert report.item_discount_total == Decimal(0)
    assert [(r.reason_name, r.discount_total) for r in report.by_reason] == [
        ("商品瑕疵", Decimal(100))
    ]
    assert [(r.clerk_user_id, r.discount_total) for r in report.by_clerk] == [
        (clerk_id, Decimal(100))
    ]


async def test_discount_without_a_reason_is_still_counted(db_session: AsyncSession) -> None:
    """原因非必填。沒填的不能從報表消失，否則正是想藏的那些看不到。"""
    store_id, clerk_id, product_id, _gift, _disc = await _seed(db_session)
    await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=1)
        ],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(450))],
        adjustments=[
            DiscountRequest(
                AdjustmentScope.ITEM, CalculationMethod.FIXED_AMOUNT, Decimal(50), target_key="0"
            )
        ],
    )
    date_from, date_to = _window()
    report = await ReportsService(db_session).discount_report(
        store_id, date_from=date_from, date_to=date_to
    )
    assert [(r.reason_id, r.reason_name) for r in report.by_reason] == [(None, "未指定原因")]
    assert report.discount_total == Decimal(50)


async def test_gift_report_groups_by_reason_and_product(db_session: AsyncSession) -> None:
    store_id, clerk_id, product_id, gift_reason, _disc = await _seed(db_session)
    await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(
                line_type=SaleLineType.CATALOG,
                catalog_product_id=product_id,
                qty=3,
                line_kind=SaleLineKind.GIFT,
                gift_reason_id=gift_reason,
            )
        ],
    )
    date_from, date_to = _window()
    report = await ReportsService(db_session).gift_report(
        store_id, date_from=date_from, date_to=date_to
    )

    assert report.gift_qty == 3
    assert report.retail_value == Decimal(1500)  # 500 × 3
    assert report.cost == Decimal(600)  # 200 × 3
    assert [(r.reason_name, r.gift_qty) for r in report.by_reason] == [("活動贈品", 3)]
    assert [(r.description, r.gift_qty) for r in report.by_product] == [("露營燈", 3)]


async def test_voided_sales_are_excluded_from_both_reports(db_session: AsyncSession) -> None:
    """作廢的單不該留在報表裡——否則折扣與贈品的統計永遠偏高。"""
    store_id, clerk_id, product_id, gift_reason, discount_reason = await _seed(db_session)
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=1),
            SaleLineInput(
                line_type=SaleLineType.CATALOG,
                catalog_product_id=product_id,
                qty=1,
                line_kind=SaleLineKind.GIFT,
                gift_reason_id=gift_reason,
            ),
        ],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(450))],
        adjustments=[
            DiscountRequest(
                AdjustmentScope.ITEM,
                CalculationMethod.FIXED_AMOUNT,
                Decimal(50),
                target_key="0",
                reason_id=discount_reason,
            )
        ],
    )
    await sales.void_sale(sale, clerk_id)

    date_from, date_to = _window()
    reports = ReportsService(db_session)
    discounts = await reports.discount_report(
        store_id, date_from=date_from, date_to=date_to
    )
    gifts = await reports.gift_report(store_id, date_from=date_from, date_to=date_to)
    assert discounts.discount_total == Decimal(0)
    assert gifts.gift_qty == 0
