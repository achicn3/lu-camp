"""C2 門市活動折扣 POS 整合測試（docs/21）：

自有序號/散裝/catalog 依活動開關套折後價；sale_line 留痕(original/discount/campaign_id)；
寄售品依 applies_consignment + bearing：預設不折；STORE_ABSORBS（payout 認原價、抽成不得<0）、
PROPORTIONAL（按折後）；無活動/DRAFT/視窗外不折。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, CatalogProduct, SerializedItem
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.models import SaleLine
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    CampaignStatus,
    ConsignmentDiscountBearing,
    Grade,
    OwnershipType,
    SaleLineType,
    SerializedItemStatus,
    TenderType,
    UserRole,
)
from app.shared.exceptions import InvalidSaleTender, SaleLineInvalid

_SEQ = 0


@pytest_asyncio.fixture
async def ctx(db_session: AsyncSession) -> dict[str, int]:
    store = Store(name="門市")
    db_session.add(store)
    await db_session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    consignor = Contact(store_id=store.id, name="寄售人", roles=["SELLER"], national_id_enc="e")
    db_session.add_all([clerk, consignor])
    await db_session.flush()
    await CashDrawerService(db_session).open_session(store.id, clerk.id, Decimal(1000))
    return {"store_id": store.id, "clerk_id": clerk.id, "consignor_id": consignor.id}


async def _make_campaign(
    session: AsyncSession,
    store_id: int,
    clerk_id: int,
    *,
    pct: int = 10,
    owned_serialized: bool = True,
    owned_bulk: bool = True,
    catalog: bool = False,
    consignment: bool = False,
    bearing: ConsignmentDiscountBearing = ConsignmentDiscountBearing.STORE_ABSORBS,
    active: bool = True,
    window: bool = True,
) -> int:
    now = datetime.now(UTC)
    starts = now - timedelta(days=1) if window else now + timedelta(days=1)
    ends = now + timedelta(days=1) if window else now + timedelta(days=2)
    svc = CampaignService(session)
    c = await svc.create_campaign(
        store_id,
        name="測試活動",
        discount_pct=pct,
        starts_at=starts,
        ends_at=ends,
        applies_owned_serialized=owned_serialized,
        applies_owned_bulk=owned_bulk,
        applies_catalog=catalog,
        applies_consignment=consignment,
        consignment_discount_bearing=bearing,
        created_by=clerk_id,
    )
    if active:
        await svc.activate(store_id, c.id, actor_user_id=clerk_id)
    return c.id


async def _serialized(
    session: AsyncSession,
    store_id: int,
    *,
    ownership: OwnershipType,
    price: str,
    consignor_id: int | None = None,
    pct: int | None = None,
) -> str:
    global _SEQ
    _SEQ += 1
    code = f"CD-{_SEQ}"
    session.add(
        SerializedItem(
            store_id=store_id,
            item_code=code,
            name="序號品",
            grade=Grade.A,
            ownership_type=ownership,
            acquisition_cost=Decimal("100") if ownership == OwnershipType.OWNED else None,
            consignor_id=consignor_id,
            commission_pct=pct,
            listed_price=Decimal(price),
            status=SerializedItemStatus.IN_STOCK,
        )
    )
    await session.flush()
    return code


async def _catalog(session: AsyncSession, store_id: int, *, price: str, qty: int = 50) -> int:
    global _SEQ
    _SEQ += 1
    p = CatalogProduct(
        store_id=store_id,
        sku=f"CDC-{_SEQ}",
        name="數量品",
        unit_price=Decimal(price),
        quantity_on_hand=qty,
    )
    session.add(p)
    await session.flush()
    return p.id


async def _bulk(
    session: AsyncSession, store_id: int, *, price: str, consignor_id: int | None = None
) -> int:
    global _SEQ
    _SEQ += 1
    lot = BulkLot(
        store_id=store_id,
        lot_code=f"CDL-{_SEQ}",
        name="散裝",
        grade=Grade.E,
        acquisition_cost=Decimal("100"),
        acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=Decimal(price),
        total_qty=20,
        remaining_qty=20,
        status=BulkLotStatus.ON_SALE,
        consignor_id=consignor_id,
    )
    session.add(lot)
    await session.flush()
    return lot.id


async def _lines_for(session: AsyncSession, sale_id: int) -> list[SaleLine]:
    return list((await session.scalars(select(SaleLine).where(SaleLine.sale_id == sale_id))).all())


async def _settlement(session: AsyncSession, sale_id: int) -> ConsignmentSettlement:
    s = await session.scalar(
        select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale_id)
    )
    assert s is not None
    return s


async def test_owned_serialized_discounted(ctx: dict[str, int], db_session: AsyncSession) -> None:
    await _make_campaign(db_session, ctx["store_id"], ctx["clerk_id"], pct=10)
    code = await _serialized(
        db_session, ctx["store_id"], ownership=OwnershipType.OWNED, price="1000"
    )
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )
    assert sale.total == Decimal(900)  # 九折
    lines = await _lines_for(db_session, sale.id)
    assert lines[0].line_total == Decimal(900)
    assert lines[0].unit_price == Decimal(900)
    assert lines[0].original_unit_price == Decimal(1000)
    assert lines[0].discount_amount == Decimal(100)
    assert lines[0].campaign_id is not None


async def test_quote_returns_discounted_total_for_pos(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """POS 結帳前以 quote 取折後總額（唯讀，不動庫存）→ 前端據此顯示折後價並送對齊的收款。"""
    await _make_campaign(db_session, ctx["store_id"], ctx["clerk_id"], pct=10)
    code = await _serialized(
        db_session, ctx["store_id"], ownership=OwnershipType.OWNED, price="1000"
    )
    quote = await SalesService(db_session).quote_sale(
        ctx["store_id"],
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )
    assert quote.total == Decimal(900)
    assert quote.campaign_name is not None
    assert quote.lines[0].line_total == Decimal(900)
    assert quote.lines[0].discount_amount == Decimal(100)
    # quote 唯讀：序號品仍在庫（未被 quote 售出）
    item = await db_session.scalar(select(SerializedItem).where(SerializedItem.item_code == code))
    assert item is not None and item.status == SerializedItemStatus.IN_STOCK
    # 用 quote 的折後總額付款 → 結帳成功（POS 正解）
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=quote.total)],
    )
    assert sale.total == Decimal(900)


async def test_full_price_tender_under_campaign_is_rejected(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """回歸：活動生效時若用『折前全額』付款（POS 未取 quote 的舊行為）→ 收款不對齊 → 422。"""
    await _make_campaign(db_session, ctx["store_id"], ctx["clerk_id"], pct=10)
    code = await _serialized(
        db_session, ctx["store_id"], ownership=OwnershipType.OWNED, price="1000"
    )
    with pytest.raises(InvalidSaleTender):
        await SalesService(db_session).create_sale(
            ctx["store_id"],
            ctx["clerk_id"],
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
            tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(1000))],
        )


async def test_no_campaign_no_discount(ctx: dict[str, int], db_session: AsyncSession) -> None:
    code = await _serialized(
        db_session, ctx["store_id"], ownership=OwnershipType.OWNED, price="1000"
    )
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )
    assert sale.total == Decimal(1000)
    lines = await _lines_for(db_session, sale.id)
    assert lines[0].original_unit_price is None
    assert lines[0].discount_amount == Decimal(0)
    assert lines[0].campaign_id is None


async def test_draft_or_out_of_window_not_applied(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    # DRAFT（未啟用）不生效
    await _make_campaign(db_session, ctx["store_id"], ctx["clerk_id"], active=False)
    code = await _serialized(
        db_session, ctx["store_id"], ownership=OwnershipType.OWNED, price="1000"
    )
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )
    assert sale.total == Decimal(1000)
    # ACTIVE 但視窗尚未開始（now < starts_at）→ get_effective 不取、亦不折
    await _make_campaign(db_session, ctx["store_id"], ctx["clerk_id"], active=True, window=False)
    code2 = await _serialized(
        db_session, ctx["store_id"], ownership=OwnershipType.OWNED, price="1000"
    )
    sale2 = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code2)],
    )
    assert sale2.total == Decimal(1000)


async def test_catalog_off_by_default_on_when_enabled(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    # 預設 catalog 關 → 不折
    await _make_campaign(db_session, ctx["store_id"], ctx["clerk_id"], pct=10, catalog=False)
    cat = await _catalog(db_session, ctx["store_id"], price="100")
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat, qty=2)],
    )
    assert sale.total == Decimal(200)  # 未折


async def test_catalog_discounted_when_enabled(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    await _make_campaign(db_session, ctx["store_id"], ctx["clerk_id"], pct=10, catalog=True)
    cat = await _catalog(db_session, ctx["store_id"], price="100")
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat, qty=2)],
    )
    assert sale.total == Decimal(180)  # 90 × 2
    lines = await _lines_for(db_session, sale.id)
    assert lines[0].discount_amount == Decimal(20)  # 10 × 2


async def test_owned_bulk_discounted_consignment_bulk_not(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    await _make_campaign(db_session, ctx["store_id"], ctx["clerk_id"], pct=10, owned_bulk=True)
    owned = await _bulk(db_session, ctx["store_id"], price="100")
    consign = await _bulk(
        db_session, ctx["store_id"], price="100", consignor_id=ctx["consignor_id"]
    )
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[
            SaleLineInput(line_type=SaleLineType.BULK_LOT, bulk_lot_id=owned, qty=2),
            SaleLineInput(line_type=SaleLineType.BULK_LOT, bulk_lot_id=consign, qty=2),
        ],
    )
    # 自有散裝 90×2=180（折）；寄售散裝 100×2=200（不折）
    assert sale.total == Decimal(380)


async def test_consignment_not_discounted_by_default(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    await _make_campaign(db_session, ctx["store_id"], ctx["clerk_id"], pct=10, consignment=False)
    code = await _serialized(
        db_session,
        ctx["store_id"],
        ownership=OwnershipType.CONSIGNMENT,
        price="1000",
        consignor_id=ctx["consignor_id"],
        pct=50,
    )
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )
    assert sale.total == Decimal(1000)  # 寄售預設不折
    s = await _settlement(db_session, sale.id)
    assert s.gross == Decimal(1000)
    assert s.commission_amount == Decimal(500)
    assert s.payout_amount == Decimal(500)


async def test_consignment_store_absorbs(ctx: dict[str, int], db_session: AsyncSession) -> None:
    """STORE_ABSORBS：客人付折後 900，寄售人 payout 認原價（commission/payout on 1000）。"""
    await _make_campaign(
        db_session,
        ctx["store_id"],
        ctx["clerk_id"],
        pct=10,
        consignment=True,
        bearing=ConsignmentDiscountBearing.STORE_ABSORBS,
    )
    code = await _serialized(
        db_session,
        ctx["store_id"],
        ownership=OwnershipType.CONSIGNMENT,
        price="1000",
        consignor_id=ctx["consignor_id"],
        pct=50,
    )
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )
    assert sale.total == Decimal(900)  # 客人付折後
    s = await _settlement(db_session, sale.id)
    assert s.gross == Decimal(1000)  # 寄售人認原價
    assert s.commission_amount == Decimal(500)
    assert s.payout_amount == Decimal(500)  # 店家收 900、付 500、淨 400 = 抽成500 − 折讓100


async def test_consignment_store_absorbs_loss_guard(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """折讓 > 原抽成 → 店家虧損 → 擋下（SaleLineInvalid）。"""
    await _make_campaign(
        db_session,
        ctx["store_id"],
        ctx["clerk_id"],
        pct=10,
        consignment=True,
        bearing=ConsignmentDiscountBearing.STORE_ABSORBS,
    )
    # 抽成 5% of 1000 = 50；折讓 10% = 100 > 50 → 虧損
    code = await _serialized(
        db_session,
        ctx["store_id"],
        ownership=OwnershipType.CONSIGNMENT,
        price="1000",
        consignor_id=ctx["consignor_id"],
        pct=5,
    )
    with pytest.raises(SaleLineInvalid):
        await SalesService(db_session).create_sale(
            ctx["store_id"],
            ctx["clerk_id"],
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
        )


async def test_consignment_proportional(ctx: dict[str, int], db_session: AsyncSession) -> None:
    """PROPORTIONAL：gross=折後，抽成與 payout 一起縮水。"""
    await _make_campaign(
        db_session,
        ctx["store_id"],
        ctx["clerk_id"],
        pct=10,
        consignment=True,
        bearing=ConsignmentDiscountBearing.PROPORTIONAL,
    )
    code = await _serialized(
        db_session,
        ctx["store_id"],
        ownership=OwnershipType.CONSIGNMENT,
        price="1000",
        consignor_id=ctx["consignor_id"],
        pct=50,
    )
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )
    assert sale.total == Decimal(900)
    s = await _settlement(db_session, sale.id)
    assert s.gross == Decimal(900)  # 折後
    assert s.commission_amount == Decimal(450)  # 50% of 900
    assert s.payout_amount == Decimal(450)


async def test_campaign_status_draft_after_create(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    cid = await _make_campaign(db_session, ctx["store_id"], ctx["clerk_id"], active=False)
    c = await CampaignService(db_session).get(ctx["store_id"], cid)
    assert c is not None and c.status == CampaignStatus.DRAFT
