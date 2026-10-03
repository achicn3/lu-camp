"""組合包袋裝條碼（ADR-028；店主 2026-10-04）：建立時驗證袋裡剛好湊成一組、掃碼回袋裡現況、
照袋裡內容結帳就套組合價並扣每件庫存。"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.campaigns.pack_service import BundlePackService
from app.modules.campaigns.schemas import (
    BundlePackItemInput,
    BundleSlotInput,
    BundleSlotTargetInput,
)
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import BulkBasket, BulkLot, CatalogProduct, SerializedItem
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import SaleBundleGroup
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    BundlePackItemType,
    CampaignKind,
    CampaignStatus,
    CampaignTargetType,
    Grade,
    OwnershipType,
    SaleLineType,
    SerializedItemStatus,
    UserRole,
)
from app.shared.exceptions import (
    BundlePackInvalid,
    BundlePackNotFound,
    CampaignConflict,
    CampaignNotFound,
    InvalidCampaignTarget,
)

_SEQ = count(1)
CODE_RE = re.compile(r"^P\d+-[0-9A-F]{10}$")


@pytest_asyncio.fixture
async def ctx(db_session: AsyncSession) -> dict[str, int]:
    store = Store(name="門市")
    other = Store(name="別家")
    db_session.add_all([store, other])
    await db_session.flush()
    manager = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    db_session.add(manager)
    birds = CatalogProduct(
        store_id=store.id,
        sku="BIRD",
        name="天堂鳥濾掛",
        unit_price=Decimal(50),
        quantity_on_hand=144,
    )
    peach = CatalogProduct(
        store_id=store.id,
        sku="PEACH",
        name="蜜桃蹦蹦濾掛",
        unit_price=Decimal(50),
        quantity_on_hand=144,
    )
    foreign = CatalogProduct(
        store_id=other.id, sku="X", name="別家的", unit_price=Decimal(50), quantity_on_hand=10
    )
    db_session.add_all([birds, peach, foreign])
    await db_session.flush()
    await CashDrawerService(db_session).open_session(store.id, manager.id, Decimal(1000))
    return {
        "store_id": store.id,
        "other_store_id": other.id,
        "manager_id": manager.id,
        "birds": birds.id,
        "peach": peach.id,
        "foreign": foreign.id,
    }


async def _campaign(
    db: AsyncSession,
    ctx: dict[str, int],
    *,
    price: int = 500,
    slots: list[tuple[int, list[tuple[CampaignTargetType, int]]]] | None = None,
    activate: bool = True,
    kind: CampaignKind = CampaignKind.BUNDLE,
) -> int:
    """預設：濾掛（天堂鳥或蜜桃都算）湊 12 包 500 元。"""
    now = datetime.now(UTC)
    svc = CampaignService(db)
    coffee = [
        (CampaignTargetType.CATALOG_PRODUCT, ctx["birds"]),
        (CampaignTargetType.CATALOG_PRODUCT, ctx["peach"]),
    ]
    bundle = kind is CampaignKind.BUNDLE
    c = await svc.create_campaign(
        ctx["store_id"],
        name="濾掛 12 入",
        discount_pct=None if bundle else 10,
        kind=kind,
        bundle_price=Decimal(price) if bundle else None,
        bundle_slots=[
            BundleSlotInput(
                qty=qty,
                targets=[BundleSlotTargetInput(target_type=t, target_id=i) for t, i in targets],
            )
            for qty, targets in (slots or [(6, coffee), (6, coffee)])
        ]
        if bundle
        else [],
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=1),
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=True,
        applies_consignment=False,
        created_by=ctx["manager_id"],
    )
    if activate:
        await svc.activate(ctx["store_id"], c.id, actor_user_id=ctx["manager_id"])
    return c.id


def _catalog(product_id: int, qty: int) -> BundlePackItemInput:
    return BundlePackItemInput(item_type=BundlePackItemType.CATALOG, target_id=product_id, qty=qty)


async def _create(
    db: AsyncSession, ctx: dict[str, int], campaign_id: int, items: list[BundlePackItemInput]
) -> int:
    pack = await BundlePackService(db).create_pack(
        ctx["store_id"],
        campaign_id,
        name="濾掛 12 入袋",
        items=items,
        actor_user_id=ctx["manager_id"],
    )
    return pack.id


async def _tent(
    db: AsyncSession, ctx: dict[str, int], *, ownership: OwnershipType = OwnershipType.OWNED
) -> SerializedItem:
    item = SerializedItem(
        store_id=ctx["store_id"],
        item_code=f"PK-{next(_SEQ)}",
        name="帳篷",
        grade=Grade.A,
        ownership_type=ownership,
        acquisition_cost=Decimal(100),
        listed_price=Decimal(3000),
        status=SerializedItemStatus.IN_STOCK,
    )
    db.add(item)
    await db.flush()
    return item


async def test_mixed_catalog_pack_gets_code_and_audit(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    campaign = await _campaign(db_session, ctx)
    service = BundlePackService(db_session)
    pack = await service.create_pack(
        ctx["store_id"],
        campaign,
        name="濾掛 12 入袋",
        items=[_catalog(ctx["birds"], 6), _catalog(ctx["peach"], 6)],
        actor_user_id=ctx["manager_id"],
    )
    assert CODE_RE.match(pack.code) and pack.code.startswith(f"P{ctx['store_id']}-")
    listed = await service.list_packs(ctx["store_id"], campaign)
    assert [(p.pack.id, [(i.target_id, i.qty) for i in p.items]) for p in listed] == [
        (pack.id, [(ctx["birds"], 6), (ctx["peach"], 6)])
    ]
    audit = await db_session.scalar(select(AuditLog).where(AuditLog.action == "bundle_pack.create"))
    assert audit is not None and audit.entity_id == str(pack.id)


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([("birds", 6), ("peach", 5)], "湊不成"),  # 少一包
        ([("birds", 7), ("peach", 6)], "多放"),  # 多一包，落在組外
    ],
)
async def test_pack_must_be_exactly_one_group(
    ctx: dict[str, int],
    db_session: AsyncSession,
    items: list[tuple[str, int]],
    message: str,
) -> None:
    campaign = await _campaign(db_session, ctx)
    with pytest.raises(BundlePackInvalid, match=message):
        await _create(db_session, ctx, campaign, [_catalog(ctx[k], q) for k, q in items])


async def test_pack_must_be_cheaper_than_list_price(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    campaign = await _campaign(db_session, ctx, price=600)  # 12 × 50 = 600，沒有比較便宜
    with pytest.raises(BundlePackInvalid, match="湊不成"):
        await _create(db_session, ctx, campaign, [_catalog(ctx["birds"], 12)])


async def test_draft_campaign_is_allowed_but_ended_or_non_bundle_is_not(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    draft = await _campaign(db_session, ctx, activate=False)
    assert await _create(db_session, ctx, draft, [_catalog(ctx["birds"], 12)])

    ended = await _campaign(db_session, ctx)
    await CampaignService(db_session).end(ctx["store_id"], ended, actor_user_id=ctx["manager_id"])
    with pytest.raises(CampaignConflict, match="已結束"):
        await _create(db_session, ctx, ended, [_catalog(ctx["birds"], 12)])

    percent = await _campaign(db_session, ctx, kind=CampaignKind.PERCENT_OFF)
    with pytest.raises(CampaignConflict, match="組合價"):
        await _create(db_session, ctx, percent, [_catalog(ctx["birds"], 12)])

    with pytest.raises(CampaignNotFound):
        await _create(db_session, ctx, 999_999, [_catalog(ctx["birds"], 12)])


async def test_items_must_belong_to_store_and_be_sellable(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    campaign = await _campaign(db_session, ctx)
    with pytest.raises(InvalidCampaignTarget):
        await _create(db_session, ctx, campaign, [_catalog(ctx["foreign"], 12)])
    with pytest.raises(BundlePackInvalid, match="重複"):
        await _create(
            db_session, ctx, campaign, [_catalog(ctx["birds"], 6), _catalog(ctx["birds"], 6)]
        )


async def test_serialized_pack_requires_owned_in_stock_and_qty_one(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    tent = await _tent(db_session, ctx)
    slots = [
        (1, [(CampaignTargetType.SERIALIZED_ITEM, tent.id)]),
        (2, [(CampaignTargetType.CATALOG_PRODUCT, ctx["birds"])]),
    ]
    campaign = await _campaign(db_session, ctx, price=3000, slots=slots)
    serialized = BundlePackItemInput(
        item_type=BundlePackItemType.SERIALIZED, target_id=tent.id, qty=1
    )
    pack = await _create(db_session, ctx, campaign, [serialized, _catalog(ctx["birds"], 2)])
    assert pack

    with pytest.raises(ValidationError, match="1 件"):
        BundlePackItemInput(item_type=BundlePackItemType.SERIALIZED, target_id=tent.id, qty=2)

    consigned = await _tent(db_session, ctx, ownership=OwnershipType.CONSIGNMENT)
    with pytest.raises(BundlePackInvalid, match="寄售"):
        await _create(
            db_session,
            ctx,
            campaign,
            [
                BundlePackItemInput(
                    item_type=BundlePackItemType.SERIALIZED, target_id=consigned.id, qty=1
                ),
                _catalog(ctx["birds"], 2),
            ],
        )

    tent.status = SerializedItemStatus.SOLD
    await db_session.flush()
    with pytest.raises(BundlePackInvalid, match="不在庫"):
        await _create(db_session, ctx, campaign, [serialized, _catalog(ctx["birds"], 2)])


async def test_basket_pack(ctx: dict[str, int], db_session: AsyncSession) -> None:
    basket = BulkBasket(
        store_id=ctx["store_id"],
        code=f"K{ctx['store_id']}-PKTEST0001",
        name="營釘",
        unit_price=Decimal(20),
    )
    db_session.add(basket)
    await db_session.flush()
    db_session.add(
        BulkLot(
            store_id=ctx["store_id"],
            lot_code=f"PKL-{next(_SEQ)}",
            name="營釘",
            grade=Grade.B,
            acquisition_cost=Decimal(100),
            acquisition_basis=BulkAcquisitionBasis.UNSPECIFIED,
            unit_price=Decimal(20),
            total_qty=30,
            remaining_qty=30,
            status=BulkLotStatus.ON_SALE,
            basket_id=basket.id,
            intake_date=datetime.now(UTC),
        )
    )
    await db_session.flush()
    slots = [
        (10, [(CampaignTargetType.BULK_BASKET, basket.id)]),
        (2, [(CampaignTargetType.CATALOG_PRODUCT, ctx["birds"])]),
    ]
    campaign = await _campaign(db_session, ctx, price=250, slots=slots)
    pack = await _create(
        db_session,
        ctx,
        campaign,
        [
            BundlePackItemInput(
                item_type=BundlePackItemType.BULK_BASKET, target_id=basket.id, qty=10
            ),
            _catalog(ctx["birds"], 2),
        ],
    )
    scan = await BundlePackService(db_session).scan(
        ctx["store_id"], (await BundlePackService(db_session).get(ctx["store_id"], pack)).code
    )
    assert [(i.item_type, i.code, i.qty, i.available) for i in scan.items] == [
        (BundlePackItemType.BULK_BASKET, basket.code, 10, True),
        (BundlePackItemType.CATALOG, "BIRD", 2, True),
    ]


async def test_scan_reports_campaign_state_and_availability(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    campaign = await _campaign(db_session, ctx)
    service = BundlePackService(db_session)
    pack_id = await _create(
        db_session, ctx, campaign, [_catalog(ctx["birds"], 6), _catalog(ctx["peach"], 6)]
    )
    code = (await service.get(ctx["store_id"], pack_id)).code

    scan = await service.scan(ctx["store_id"], code)
    assert scan.campaign_effective is True
    assert scan.bundle_price == Decimal(500)
    assert [(i.name, i.unit_price, i.qty, i.available) for i in scan.items] == [
        ("天堂鳥濾掛", Decimal(50), 6, True),
        ("蜜桃蹦蹦濾掛", Decimal(50), 6, True),
    ]

    peach = await db_session.get(CatalogProduct, ctx["peach"])
    assert peach is not None
    peach.quantity_on_hand = 5
    await db_session.flush()
    short = await service.scan(ctx["store_id"], code)
    assert [(i.available, i.unavailable_reason) for i in short.items][1] == (False, "庫存只剩 5")

    await CampaignService(db_session).end(
        ctx["store_id"], campaign, actor_user_id=ctx["manager_id"]
    )
    assert (await service.scan(ctx["store_id"], code)).campaign_effective is False

    # 別家掃不到；停用後掃不到
    with pytest.raises(BundlePackNotFound):
        await service.scan(ctx["other_store_id"], code)
    await service.deactivate(ctx["store_id"], pack_id, actor_user_id=ctx["manager_id"])
    with pytest.raises(BundlePackNotFound):
        await service.scan(ctx["store_id"], code)


async def test_selling_the_scanned_contents_applies_bundle_and_deducts_each_item(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    campaign = await _campaign(db_session, ctx)
    service = BundlePackService(db_session)
    pack_id = await _create(
        db_session, ctx, campaign, [_catalog(ctx["birds"], 6), _catalog(ctx["peach"], 6)]
    )
    scan = await service.scan(ctx["store_id"], (await service.get(ctx["store_id"], pack_id)).code)
    lines = [
        SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=i.target_id, qty=i.qty)
        for i in scan.items
    ]
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["manager_id"], lines=lines
    )
    assert sale.total == Decimal(500)
    group = await db_session.scalar(
        select(SaleBundleGroup).where(SaleBundleGroup.sale_id == sale.id)
    )
    assert group is not None and group.campaign_id == campaign
    for key in ("birds", "peach"):
        product = await db_session.get(CatalogProduct, ctx[key])
        assert product is not None and product.quantity_on_hand == 138


async def test_list_shows_campaign_status_independent_packs(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """同一個活動可以有好幾袋；停用的也列出（標示停用），別的活動的不列。"""
    campaign = await _campaign(db_session, ctx)
    other = await _campaign(db_session, ctx)
    service = BundlePackService(db_session)
    first = await _create(db_session, ctx, campaign, [_catalog(ctx["birds"], 12)])
    second = await _create(db_session, ctx, campaign, [_catalog(ctx["peach"], 12)])
    await _create(db_session, ctx, other, [_catalog(ctx["birds"], 12)])
    await service.deactivate(ctx["store_id"], first, actor_user_id=ctx["manager_id"])
    listed = await service.list_packs(ctx["store_id"], campaign)
    assert [(p.pack.id, p.pack.is_active) for p in listed] == [(first, False), (second, True)]
    status = await CampaignService(db_session).get(ctx["store_id"], campaign)
    assert status is not None and status.status is CampaignStatus.ACTIVE
