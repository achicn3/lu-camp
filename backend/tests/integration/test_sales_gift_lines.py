"""結帳時的贈品行（P2）：實際出庫、成交 0 元、可統計。

需求書第一條原則：贈品**不是** 100% 折扣、不是負數明細、不是把售價改成 0，而是一種
獨立的明細性質——它會扣庫存、留下原價與成本、但實收為 0。
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import GiftReason, SaleLine
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    Grade,
    OwnershipType,
    SaleLineKind,
    SaleLineType,
    StockReason,
    UserRole,
)
from app.shared.exceptions import SaleLineInvalid


async def _seed(session: AsyncSession) -> tuple[int, int, int, int]:
    """門市＋店長＋開帳＋一般商品（有成本）＋預設贈品原因。"""
    store = Store(name="贈品店", tax_id="12345678")
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id, username=f"g-{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    session.add(clerk)
    session.add(StoreSettings(store_id=store.id))
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("2000"))
    product = CatalogProduct(
        store_id=store.id,
        sku=f"GF-{store.id}",
        name="小物",
        unit_price=Decimal("300"),
        unit_cost=Decimal("120"),
        quantity_on_hand=20,
    )
    reason = GiftReason(store_id=store.id, code="PROMOTION", name="活動贈品")
    session.add_all([product, reason])
    await session.flush()
    return store.id, clerk.id, product.id, reason.id


def _gift(product_id: int, reason_id: int, qty: int = 1, note: str | None = None) -> SaleLineInput:
    return SaleLineInput(
        line_type=SaleLineType.CATALOG,
        catalog_product_id=product_id,
        qty=qty,
        line_kind=SaleLineKind.GIFT,
        gift_reason_id=reason_id,
        gift_note=note,
    )


async def test_gift_line_leaves_stock_but_is_not_charged(db_session: AsyncSession) -> None:
    """贈品要扣庫存、留原價與成本，但不增加實收。"""
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    before = (await db_session.get(CatalogProduct, product_id)).quantity_on_hand  # type: ignore[union-attr]

    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(
                line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=2
            ),
            _gift(product_id, reason_id, qty=1, note="週年慶"),
        ],
    )

    # 實收只算一般銷售：300 × 2 = 600（贈品 0）
    assert sale.total == Decimal(600)

    lines = (
        await db_session.scalars(
            select(SaleLine).where(SaleLine.sale_id == sale.id).order_by(SaleLine.id)
        )
    ).all()
    normal, gift = lines
    assert normal.line_kind is SaleLineKind.NORMAL
    assert gift.line_kind is SaleLineKind.GIFT
    assert gift.unit_price == Decimal(0)
    assert gift.line_total == Decimal(0)
    assert gift.net_amount == Decimal(0)
    assert gift.discount_amount == Decimal(0)  # 贈品價值不得混入折扣
    assert gift.original_unit_price == Decimal(300)  # 原價留痕
    assert gift.cost_snapshot == Decimal(120)  # 成本留痕
    assert gift.gift_reason_name == "活動贈品"  # 名稱快照
    assert gift.gift_note == "週年慶"

    # 庫存：一般 2 件 + 贈品 1 件都要出庫
    after = (await db_session.get(CatalogProduct, product_id)).quantity_on_hand  # type: ignore[union-attr]
    assert after == before - 3


async def test_gift_writes_a_distinguishable_stock_movement(db_session: AsyncSession) -> None:
    """贈品出庫要能與一般銷售分辨，否則報表統計不出贈了幾件。"""
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=1),
            _gift(product_id, reason_id, qty=2),
        ],
    )
    moves = (
        await db_session.scalars(
            select(StockMovement)
            .where(StockMovement.ref_type == "sale", StockMovement.ref_id == sale.id)
            .order_by(StockMovement.id)
        )
    ).all()
    assert [(m.reason, m.qty) for m in moves] == [
        (StockReason.SALE, 1),
        (StockReason.GIFT, 2),
    ]


async def test_gift_requires_a_reason(db_session: AsyncSession) -> None:
    """送東西一定要說明為什麼——事後唯一能追的就是這個。"""
    store_id, clerk_id, product_id, _reason_id = await _seed(db_session)
    with pytest.raises(SaleLineInvalid, match="原因"):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=[
                SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=1),
                SaleLineInput(
                    line_type=SaleLineType.CATALOG,
                    catalog_product_id=product_id,
                    qty=1,
                    line_kind=SaleLineKind.GIFT,
                ),
            ],
        )


async def test_gift_reason_must_belong_to_this_store(db_session: AsyncSession) -> None:
    store_id, clerk_id, product_id, _reason_id = await _seed(db_session)
    other_store_id, _c, _p, other_reason_id = await _seed(db_session)
    assert other_store_id != store_id
    with pytest.raises(SaleLineInvalid, match="原因"):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=[
                SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=1),
                _gift(product_id, other_reason_id),
            ],
        )


async def test_consignment_item_may_not_be_given_away(db_session: AsyncSession) -> None:
    """寄售品不參與任何折扣或贈送——那是拿別人的貨做人情，寄售人會拿 0。"""
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    from app.modules.contacts.models import Contact

    consignor = Contact(store_id=store_id, name="寄售人", roles=["CONSIGNOR"], phone="0911222333")
    db_session.add(consignor)
    await db_session.flush()
    item = await InventoryService(db_session).create_serialized_item(
        store_id,
        item_code=f"CON-{store_id}",
        name="寄售帳篷",
        grade=Grade.A,
        ownership_type=OwnershipType.CONSIGNMENT,
        listed_price=Decimal("2000"),
        consignor_id=consignor.id,
        commission_pct=50,
    )
    with pytest.raises(SaleLineInvalid, match="寄售"):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=[
                SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=1),
                SaleLineInput(
                    line_type=SaleLineType.SERIALIZED,
                    item_code=item.item_code,
                    line_kind=SaleLineKind.GIFT,
                    gift_reason_id=reason_id,
                ),
            ],
        )


async def test_a_sale_of_only_gifts_is_allowed_and_takes_no_payment(
    db_session: AsyncSession,
) -> None:
    """全贈品單（店主裁示要支援）：應付 0、沒有收款明細、不開發票。"""
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    sales = SalesService(db_session)

    sale = await sales.create_sale(
        store_id, clerk_id, lines=[_gift(product_id, reason_id, qty=3)]
    )

    assert sale.total == Decimal(0)
    assert sale.subtotal == Decimal(0)
    assert sale.tax == Decimal(0)
    tenders = await sales.get_tenders(sale.id)
    assert tenders == []  # 零元單不得有收款明細
    assert sale.awarded_points == 0
    # 庫存仍要扣
    product = await db_session.get(CatalogProduct, product_id)
    assert product is not None and product.quantity_on_hand == 17


async def test_gift_only_sale_does_not_create_an_invoice(db_session: AsyncSession) -> None:
    """發票總額必須 > 0（DB CHECK），零元單本來就不該開發票。"""
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
        lines=[_gift(product_id, reason_id)],
        expected_einvoice_enabled=True,
    )
    from app.modules.einvoice.service import EInvoiceService

    invoice = await EInvoiceService(db_session).get_invoice_for_sale(store_id, sale.id)
    assert invoice is None


async def test_buying_and_gifting_the_same_product_are_two_separate_lines(
    db_session: AsyncSession,
) -> None:
    """同一商品「買 2 ＋ 送 1」必須是兩筆，不可被合併。

    客顯購物車的差異比對以 item_key 當字典鍵、前端也用它合併同款商品——鍵若不含
    商業性質，其中一筆會被靜默吃掉（買的變成送的，或反之）。
    """
    store_id, clerk_id, product_id, reason_id = await _seed(db_session)
    sales = SalesService(db_session)
    normal = SaleLineInput(
        line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=2
    )
    sale = await sales.create_sale(
        store_id, clerk_id, lines=[normal, _gift(product_id, reason_id, qty=1)]
    )
    lines = await _lines(db_session, sale.id)
    assert len(lines) == 2
    assert [line.line_kind for line in lines] == [SaleLineKind.NORMAL, SaleLineKind.GIFT]
    # 兩筆的購物車鍵必須不同，否則差異比對會把它們當成同一個項目
    keys = {sales._cart_item_key(normal), sales._cart_item_key(_gift(product_id, reason_id))}
    assert len(keys) == 2


async def _lines(session: AsyncSession, sale_id: int) -> list[SaleLine]:
    return list(
        (
            await session.scalars(
                select(SaleLine).where(SaleLine.sale_id == sale_id).order_by(SaleLine.id)
            )
        ).all()
    )
