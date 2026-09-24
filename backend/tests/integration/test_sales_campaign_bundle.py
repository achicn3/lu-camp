"""門市活動 v2 P4 組合價：結帳、報價、整組退（docs/40 §4、§8；裁示 5、6）。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campaigns.schemas import BundleSlotInput, BundleSlotTargetInput
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import Brand, CatalogProduct, ProductModel, SerializedItem
from app.modules.reports.service import ReportsService
from app.modules.returns.models import CustomerReturn
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import SaleBundleGroup, SaleBundleMember, SaleLine
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    CampaignKind,
    CampaignTargetType,
    Grade,
    OwnershipType,
    SaleLineType,
    SerializedItemStatus,
    UserRole,
)
from app.shared.exceptions import ReturnLineInvalid

_SEQ = count(1)


@pytest_asyncio.fixture
async def ctx(db_session: AsyncSession) -> dict[str, int]:
    store = Store(name="門市")
    db_session.add(store)
    await db_session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    brand = Brand(store_id=store.id, name="Snow Peak")
    db_session.add_all([clerk, brand])
    await db_session.flush()
    tent = ProductModel(store_id=store.id, brand_id=brand.id, name="Amenity Dome")
    db_session.add(tent)
    gas = CatalogProduct(
        store_id=store.id, sku="GAS", name="瓦斯罐", unit_price=Decimal(100), quantity_on_hand=20
    )
    db_session.add(gas)
    await db_session.flush()
    await CashDrawerService(db_session).open_session(store.id, clerk.id, Decimal(1000))
    return {"store_id": store.id, "clerk_id": clerk.id, "tent": tent.id, "gas": gas.id}


async def _bundle(db: AsyncSession, ctx: dict[str, int], price: int, gas_qty: int) -> int:
    now = datetime.now(UTC)
    svc = CampaignService(db)
    c = await svc.create_campaign(
        ctx["store_id"],
        name="帳篷＋瓦斯組",
        discount_pct=None,
        kind=CampaignKind.BUNDLE,
        bundle_price=Decimal(price),
        bundle_slots=[
            BundleSlotInput(
                qty=1,
                targets=[
                    BundleSlotTargetInput(
                        target_type=CampaignTargetType.PRODUCT_MODEL, target_id=ctx["tent"]
                    )
                ],
            ),
            BundleSlotInput(
                qty=gas_qty,
                targets=[
                    BundleSlotTargetInput(
                        target_type=CampaignTargetType.CATALOG_PRODUCT, target_id=ctx["gas"]
                    )
                ],
            ),
        ],
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=1),
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=True,
        applies_consignment=False,
        created_by=ctx["clerk_id"],
    )
    await svc.activate(ctx["store_id"], c.id, actor_user_id=ctx["clerk_id"])
    return c.id


async def _tent(db: AsyncSession, ctx: dict[str, int], price: str = "6000") -> SerializedItem:
    item = SerializedItem(
        store_id=ctx["store_id"],
        item_code=f"BD-{next(_SEQ)}",
        name="帳篷",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        acquisition_cost=Decimal(100),
        listed_price=Decimal(price),
        status=SerializedItemStatus.IN_STOCK,
        product_model_id=ctx["tent"],
    )
    db.add(item)
    await db.flush()
    return item


def _lines(tent: SerializedItem, ctx: dict[str, int], gas_qty: int) -> list[SaleLineInput]:
    return [
        SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=tent.item_code),
        SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=ctx["gas"], qty=gas_qty),
    ]


async def _line_ids(db: AsyncSession, sale_id: int) -> list[int]:
    return list(
        (
            await db.scalars(
                select(SaleLine.id).where(SaleLine.sale_id == sale_id).order_by(SaleLine.id)
            )
        ).all()
    )


async def _return(
    db: AsyncSession, ctx: dict[str, int], sale_id: int, lines: list[tuple[int, int]], key: str
) -> CustomerReturn:
    return await ReturnsService(db).create_return(
        ctx["store_id"],
        sale_id=sale_id,
        lines=[ReturnLineInput(sale_line_id=lid, qty=q) for lid, q in lines],
        reason="不要了",
        actor_user_id=ctx["clerk_id"],
        idempotency_key=key,
    )


async def test_checkout_records_bundle_group_and_quote_matches(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    campaign = await _bundle(db_session, ctx, 6000, 2)
    tent = await _tent(db_session, ctx)
    lines = _lines(tent, ctx, 3)
    service = SalesService(db_session)

    quote = await service.quote_sale(ctx["store_id"], lines=lines)
    sale = await service.create_sale(ctx["store_id"], ctx["clerk_id"], lines=lines)

    # 帳篷 6000＋2 罐 200 → 組合 6000；第 3 罐原價 100
    assert quote.total == sale.total == Decimal(6100)
    assert [ql.bundle_groups for ql in quote.lines] == [((0, campaign, 1),), ((0, campaign, 2),)]
    groups = (
        await db_session.scalars(select(SaleBundleGroup).where(SaleBundleGroup.sale_id == sale.id))
    ).all()
    assert [(g.campaign_id, g.bundle_price, g.returned_return_id) for g in groups] == [
        (campaign, Decimal(6000), None)
    ]
    members = (
        await db_session.scalars(
            select(SaleBundleMember.qty)
            .where(SaleBundleMember.bundle_group_id == groups[0].id)
            .order_by(SaleBundleMember.sale_line_id)
        )
    ).all()
    assert list(members) == [1, 2]


async def test_returning_part_of_a_bundle_is_rejected(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    await _bundle(db_session, ctx, 6000, 2)
    tent = await _tent(db_session, ctx)
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=_lines(tent, ctx, 2)
    )
    tent_line, _gas_line = await _line_ids(db_session, sale.id)
    with pytest.raises(ReturnLineInvalid, match="整組"):
        await _return(db_session, ctx, sale.id, [(tent_line, 1)], "bd-part")


async def test_returning_the_whole_bundle_marks_it_returned(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    await _bundle(db_session, ctx, 6000, 2)
    tent = await _tent(db_session, ctx)
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=_lines(tent, ctx, 2)
    )
    tent_line, gas_line = await _line_ids(db_session, sale.id)
    result = await _return(db_session, ctx, sale.id, [(tent_line, 1), (gas_line, 2)], "bd-all")
    assert result.refund_amount == Decimal(6000)
    group = await db_session.scalar(
        select(SaleBundleGroup).where(SaleBundleGroup.sale_id == sale.id)
    )
    assert group is not None and group.returned_return_id == result.id


async def test_unbundled_units_of_a_line_can_be_returned_alone(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """3 罐只有 2 罐在組內：退 1 罐可以（算組外那罐），退第 2 罐就碰到組合、要整組退。"""
    await _bundle(db_session, ctx, 6000, 2)
    tent = await _tent(db_session, ctx)
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=_lines(tent, ctx, 3)
    )
    tent_line, gas_line = await _line_ids(db_session, sale.id)
    loose = await _return(db_session, ctx, sale.id, [(gas_line, 1)], "bd-loose")
    with pytest.raises(ReturnLineInvalid, match="整組"):
        await _return(db_session, ctx, sale.id, [(gas_line, 1)], "bd-loose-2")
    rest = await _return(db_session, ctx, sale.id, [(tent_line, 1), (gas_line, 2)], "bd-rest")
    # 全部退完：兩次退款加起來剛好是整筆實付，不多不少
    assert loose.refund_amount + rest.refund_amount == sale.total


async def test_campaign_report_counts_bundles_sold_net_of_returns(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    campaign = await _bundle(db_session, ctx, 6000, 2)
    service = SalesService(db_session)
    kept = await service.create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=_lines(await _tent(db_session, ctx), ctx, 2)
    )
    returned = await service.create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=_lines(await _tent(db_session, ctx), ctx, 2)
    )
    tent_line, gas_line = await _line_ids(db_session, returned.id)
    await _return(db_session, ctx, returned.id, [(tent_line, 1), (gas_line, 2)], "bd-rpt")

    row = next(
        r
        for r in (await ReportsService(db_session).campaign_performance(ctx["store_id"])).rows
        if r.campaign_id == campaign
    )
    assert (row.kind, row.bundle_price, row.bundles_sold) == ("BUNDLE", Decimal(6000), 1)
    assert row.transaction_count == 1
    assert kept.total == Decimal(6000)


async def test_preview_already_warns_about_partial_bundle(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    await _bundle(db_session, ctx, 6000, 2)
    tent = await _tent(db_session, ctx)
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=_lines(tent, ctx, 2)
    )
    tent_line, gas_line = await _line_ids(db_session, sale.id)
    service = ReturnsService(db_session)
    with pytest.raises(ReturnLineInvalid, match="整組"):
        await service.preview_return(
            ctx["store_id"], sale_id=sale.id, lines=[ReturnLineInput(sale_line_id=tent_line, qty=1)]
        )
    preview = await service.preview_return(
        ctx["store_id"],
        sale_id=sale.id,
        lines=[
            ReturnLineInput(sale_line_id=tent_line, qty=1),
            ReturnLineInput(sale_line_id=gas_line, qty=2),
        ],
    )
    assert preview["refund_total"] == Decimal(6000)
