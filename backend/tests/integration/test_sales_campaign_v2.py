"""門市活動 v2 結帳／報價整合（docs/40 P1a，2026-09-23 裁示）。

- 多個活動同時生效；範圍可細到品牌／單件等（包含／排除）。
- 不可疊加＝不跟任何活動併用；可疊加全部連乘；每件取對客人最划算的。
- 每行套到哪些活動、各折多少寫進 sale_line_campaigns（Σ＝sale_lines.discount_amount）；
  活動成效報表依它歸屬（疊加時兩個活動各記各的折讓）。
- 報價與結帳同價。
"""

from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.campaigns.schemas import CampaignTargetInput
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.service import ConsignmentService
from app.modules.inventory.models import Brand, CatalogProduct, SerializedItem
from app.modules.reports.service import ReportsService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import CampaignOverrideInput, SaleLineInput
from app.modules.sales.models import SaleLine, SaleLineCampaign
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    CampaignKind,
    CampaignTargetMode,
    CampaignTargetType,
    Grade,
    OwnershipType,
    SaleLineType,
    SerializedItemStatus,
    UserRole,
)

_SEQ = count(1)


@pytest_asyncio.fixture
async def ctx(db_session: AsyncSession) -> dict[str, int]:
    store = Store(name="門市")
    db_session.add(store)
    await db_session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    db_session.add(clerk)
    await db_session.flush()
    await CashDrawerService(db_session).open_session(store.id, clerk.id, Decimal(1000))
    return {"store_id": store.id, "clerk_id": clerk.id}


async def _campaign(
    db: AsyncSession,
    ctx: dict[str, int],
    pct: int,
    *,
    name: str | None = None,
    stackable: bool = False,
    targets: list[CampaignTargetInput] | None = None,
) -> int:
    now = datetime.now(UTC)
    svc = CampaignService(db)
    c = await svc.create_campaign(
        ctx["store_id"],
        name=name or f"{pct}% 活動",
        discount_pct=pct,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=1),
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=False,
        applies_consignment=False,
        created_by=ctx["clerk_id"],
        stackable=stackable,
        targets=targets,
    )
    await svc.activate(ctx["store_id"], c.id, actor_user_id=ctx["clerk_id"])
    return c.id


async def _item(
    db: AsyncSession, store_id: int, price: str, *, brand_id: int | None = None
) -> SerializedItem:
    item = SerializedItem(
        store_id=store_id,
        item_code=f"V2-{next(_SEQ)}",
        name="序號品",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        acquisition_cost=Decimal(100),
        listed_price=Decimal(price),
        status=SerializedItemStatus.IN_STOCK,
        brand_id=brand_id,
    )
    db.add(item)
    await db.flush()
    return item


def _line(item: SerializedItem) -> SaleLineInput:
    return SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=item.item_code)


async def _allocations(db: AsyncSession, sale_id: int) -> dict[int, list[tuple[int, Decimal]]]:
    rows = (
        await db.execute(
            select(
                SaleLine.serialized_item_id,
                SaleLineCampaign.campaign_id,
                SaleLineCampaign.discount_amount,
            )
            .join(SaleLineCampaign, SaleLineCampaign.sale_line_id == SaleLine.id)
            .where(SaleLine.sale_id == sale_id)
            .order_by(SaleLineCampaign.id)
        )
    ).all()
    out: dict[int, list[tuple[int, Decimal]]] = {}
    for item_id, campaign_id, amount in rows:
        out.setdefault(item_id, []).append((campaign_id, Decimal(amount)))
    return out


async def test_two_campaigns_at_once_each_item_gets_its_best(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    brand = Brand(store_id=ctx["store_id"], name="Snow Peak")
    db_session.add(brand)
    await db_session.flush()
    storewide = await _campaign(db_session, ctx, 10, name="全館九折")
    brand_sale = await _campaign(
        db_session,
        ctx,
        30,
        name="Snow Peak 七折",
        targets=[
            CampaignTargetInput(
                mode=CampaignTargetMode.INCLUDE,
                target_type=CampaignTargetType.BRAND,
                target_id=brand.id,
            )
        ],
    )
    sp = await _item(db_session, ctx["store_id"], "1000", brand_id=brand.id)
    other = await _item(db_session, ctx["store_id"], "1000")

    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[_line(sp), _line(other)]
    )
    assert sale.total == Decimal(700 + 900)
    alloc = await _allocations(db_session, sale.id)
    assert alloc[sp.id] == [(brand_sale, Decimal(300))]
    assert alloc[other.id] == [(storewide, Decimal(100))]


async def test_stackable_campaigns_multiply_and_both_are_recorded(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    a = await _campaign(db_session, ctx, 10, stackable=True, name="全館九折")
    b = await _campaign(db_session, ctx, 10, stackable=True, name="會員九折")
    item = await _item(db_session, ctx["store_id"], "1000")

    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[_line(item)]
    )
    assert sale.total == Decimal(810)
    assert (await _allocations(db_session, sale.id))[item.id] == [
        (a, Decimal(100)),
        (b, Decimal(90)),
    ]
    line = await db_session.scalar(select(SaleLine).where(SaleLine.sale_id == sale.id))
    assert line is not None
    assert line.discount_amount == Decimal(190)
    assert line.original_unit_price == Decimal(1000)
    assert line.campaign_id == a  # 貢獻最多的那個（舊欄位相容）


async def test_non_stackable_is_not_combined(ctx: dict[str, int], db_session: AsyncSession) -> None:
    await _campaign(db_session, ctx, 10, stackable=True)
    await _campaign(db_session, ctx, 10, stackable=True)
    exclusive = await _campaign(db_session, ctx, 30, name="七折（不可併用）")
    item = await _item(db_session, ctx["store_id"], "1000")

    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[_line(item)]
    )
    assert sale.total == Decimal(700)
    assert (await _allocations(db_session, sale.id))[item.id] == [(exclusive, Decimal(300))]


async def test_excluded_item_is_not_discounted(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    keep_full = await _item(db_session, ctx["store_id"], "1000")
    other = await _item(db_session, ctx["store_id"], "1000")
    await _campaign(
        db_session,
        ctx,
        10,
        targets=[
            CampaignTargetInput(
                mode=CampaignTargetMode.EXCLUDE,
                target_type=CampaignTargetType.SERIALIZED_ITEM,
                target_id=keep_full.id,
            )
        ],
    )
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[_line(keep_full), _line(other)]
    )
    assert sale.total == Decimal(1000 + 900)
    alloc = await _allocations(db_session, sale.id)
    assert keep_full.id not in alloc


async def test_quote_matches_checkout_and_names_the_campaigns(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    await _campaign(db_session, ctx, 10, stackable=True, name="全館九折")
    await _campaign(db_session, ctx, 10, stackable=True, name="會員九折")
    item = await _item(db_session, ctx["store_id"], "1000")

    quote = await SalesService(db_session).quote_sale(ctx["store_id"], lines=[_line(item)])
    assert quote.total == Decimal(810)
    assert [(c.name, c.discount_amount) for c in quote.lines[0].campaigns] == [
        ("全館九折", Decimal(100)),
        ("會員九折", Decimal(90)),
    ]
    assert [(c.name, c.discount_amount) for c in quote.campaigns] == [
        ("全館九折", Decimal(100)),
        ("會員九折", Decimal(90)),
    ]
    assert quote.campaign_name == "全館九折、會員九折"

    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[_line(item)]
    )
    assert sale.total == quote.total


async def test_campaign_report_credits_each_stacked_campaign(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    a = await _campaign(db_session, ctx, 10, stackable=True)
    b = await _campaign(db_session, ctx, 10, stackable=True)
    item = await _item(db_session, ctx["store_id"], "1000")
    await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[_line(item)]
    )

    report = await ReportsService(db_session).campaign_performance(ctx["store_id"])
    totals = {row.campaign_id: row.campaign_discount_total for row in report.rows}
    assert totals[a] == Decimal(100)
    assert totals[b] == Decimal(90)


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_quote_api_lists_campaigns_per_line_and_for_the_whole_sale(
    ctx: dict[str, int], db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    a = await _campaign(db_session, ctx, 10, stackable=True, name="全館九折")
    b = await _campaign(db_session, ctx, 10, stackable=True, name="會員九折")
    item = await _item(db_session, ctx["store_id"], "1000")
    token = encode_access_token(user_id=ctx["clerk_id"], role="CLERK", store_id=ctx["store_id"])

    resp = await client.post(
        "/api/v1/sales/quote",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": item.item_code}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected = [
        {"campaign_id": a, "name": "全館九折", "discount_amount": "100"},
        {"campaign_id": b, "name": "會員九折", "discount_amount": "90"},
    ]
    assert body["lines"][0]["campaigns"] == expected
    assert body["campaigns"] == expected
    assert body["campaign_name"] == "全館九折、會員九折"


async def test_campaign_report_only_counts_lines_the_campaign_applied_to(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """同時開兩個不同品牌的活動：各自只算自己品牌的成交；沒賣出的活動全為 0（Codex 審查）。"""
    sp = Brand(store_id=ctx["store_id"], name="Snow Peak")
    coleman = Brand(store_id=ctx["store_id"], name="Coleman")
    idle = Brand(store_id=ctx["store_id"], name="沒人買的牌子")
    db_session.add_all([sp, coleman, idle])
    await db_session.flush()

    def only(brand: Brand) -> list[CampaignTargetInput]:
        return [
            CampaignTargetInput(
                mode=CampaignTargetMode.INCLUDE,
                target_type=CampaignTargetType.BRAND,
                target_id=brand.id,
            )
        ]

    sp_sale = await _campaign(db_session, ctx, 30, name="Snow Peak 七折", targets=only(sp))
    coleman_sale = await _campaign(db_session, ctx, 20, name="Coleman 八折", targets=only(coleman))
    idle_sale = await _campaign(db_session, ctx, 10, name="沒賣出", targets=only(idle))
    a = await _item(db_session, ctx["store_id"], "1000", brand_id=sp.id)
    b = await _item(db_session, ctx["store_id"], "2000", brand_id=coleman.id)
    c = await _item(db_session, ctx["store_id"], "500")  # 沒有活動
    svc = SalesService(db_session)
    await svc.create_sale(ctx["store_id"], ctx["clerk_id"], lines=[_line(a), _line(c)])
    await svc.create_sale(ctx["store_id"], ctx["clerk_id"], lines=[_line(b)])

    rows = {
        r.campaign_id: r
        for r in (await ReportsService(db_session).campaign_performance(ctx["store_id"])).rows
    }
    assert rows[sp_sale].gross_turnover == Decimal(700)
    assert rows[sp_sale].gross_margin == Decimal(600)  # 700 − 成本 100
    assert rows[sp_sale].transaction_count == 1
    assert rows[coleman_sale].gross_turnover == Decimal(1600)
    assert rows[coleman_sale].recognized_revenue == Decimal(1600)
    assert rows[coleman_sale].transaction_count == 1
    assert rows[idle_sale].gross_turnover == Decimal(0)
    assert rows[idle_sale].gross_margin == Decimal(0)
    assert rows[idle_sale].transaction_count == 0
    assert rows[idle_sale].gross_margin_rate is None


async def test_campaign_report_deducts_returned_lines(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    campaign = await _campaign(db_session, ctx, 10)
    kept = await _item(db_session, ctx["store_id"], "1000")
    returned = await _item(db_session, ctx["store_id"], "1000")
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[_line(kept), _line(returned)]
    )
    line_id = await db_session.scalar(
        select(SaleLine.id).where(
            SaleLine.sale_id == sale.id, SaleLine.serialized_item_id == returned.id
        )
    )
    assert line_id is not None
    await ReturnsService(db_session).create_return(
        ctx["store_id"],
        sale_id=sale.id,
        lines=[ReturnLineInput(sale_line_id=line_id, qty=1)],
        reason="尺寸不合",
        actor_user_id=ctx["clerk_id"],
        idempotency_key="v2-return-1",
    )

    row = next(
        r
        for r in (await ReportsService(db_session).campaign_performance(ctx["store_id"])).rows
        if r.campaign_id == campaign
    )
    assert row.gross_turnover == Decimal(900)
    assert row.gross_margin == Decimal(800)
    assert row.transaction_count == 1


async def test_report_lookups_survive_more_ids_than_the_driver_parameter_limit(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """asyncpg 一次查詢最多 32,767 個參數；累積多年的活動明細不能讓報表整張打不開（Codex 審查）。"""
    many = list(range(1, 40_001))
    assert await ReturnsService(db_session).returned_qty_by_line_ids(ctx["store_id"], many) == {}
    assert (
        await ConsignmentService(db_session).effective_commission_by_sale_item(
            ctx["store_id"], many
        )
        == {}
    )


async def test_campaign_report_shows_stackable_scope_and_not_applied_count(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """報表每列：可疊加、指定範圍（附名稱）、店員「這筆不套用」的次數（docs/40 P1d）。"""
    brand = Brand(store_id=ctx["store_id"], name="Snow Peak")
    db_session.add(brand)
    await db_session.flush()
    targeted = await _campaign(
        db_session,
        ctx,
        20,
        name="Snow Peak 八折",
        stackable=True,
        targets=[
            CampaignTargetInput(
                mode=CampaignTargetMode.INCLUDE,
                target_type=CampaignTargetType.BRAND,
                target_id=brand.id,
            )
        ],
    )
    item = await _item(db_session, ctx["store_id"], "1000", brand_id=brand.id)
    await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[_line(item)],
        disabled_campaigns=[CampaignOverrideInput(campaign_id=targeted, reason="客人不要")],
    )

    row = next(
        r
        for r in (await ReportsService(db_session).campaign_performance(ctx["store_id"])).rows
        if r.campaign_id == targeted
    )
    assert row.stackable is True
    assert [(t.mode, t.label) for t in row.targets] == [("INCLUDE", "Snow Peak")]
    assert row.not_applied_count == 1
    assert row.transaction_count == 0  # 被取消了，這筆不算它的成效


async def test_fixed_price_and_amount_off_at_checkout(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """P2：特價與折金額實際結帳；和九折同時進行時取最划算的。"""
    now = datetime.now(UTC)
    svc = CampaignService(db_session)
    fixed = await svc.create_campaign(
        ctx["store_id"],
        name="特價 690",
        discount_pct=None,
        kind=CampaignKind.FIXED_PRICE,
        fixed_price=Decimal(690),
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=1),
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=False,
        applies_consignment=False,
        created_by=ctx["clerk_id"],
    )
    await svc.activate(ctx["store_id"], fixed.id, actor_user_id=ctx["clerk_id"])
    await _campaign(db_session, ctx, 10, name="全館九折")
    item = await _item(db_session, ctx["store_id"], "1000")

    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[_line(item)]
    )
    assert sale.total == Decimal(690)
    assert (await _allocations(db_session, sale.id))[item.id] == [(fixed.id, Decimal(310))]


# ── P3：買 N 送 M ───────────────────────────────────────────────────


async def _bngm(
    db: AsyncSession, ctx: dict[str, int], buy: int, free: int, *, stackable: bool = False
) -> int:
    now = datetime.now(UTC)
    svc = CampaignService(db)
    c = await svc.create_campaign(
        ctx["store_id"],
        name=f"買{buy}送{free}",
        discount_pct=None,
        kind=CampaignKind.BUY_N_GET_M,
        buy_qty=buy,
        free_qty=free,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=1),
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=True,
        applies_consignment=False,
        created_by=ctx["clerk_id"],
        stackable=stackable,
    )
    await svc.activate(ctx["store_id"], c.id, actor_user_id=ctx["clerk_id"])
    return c.id


async def test_buy_n_get_m_allocates_free_amount_and_quote_matches(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """買二送一：1000／600／400 → 送 400，按比例分到三件（200／120／80），報價與結帳同價。"""
    campaign = await _bngm(db_session, ctx, 2, 1)
    items = [await _item(db_session, ctx["store_id"], p) for p in ("1000", "600", "400")]
    lines = [_line(i) for i in items]
    service = SalesService(db_session)

    quote = await service.quote_sale(ctx["store_id"], lines=lines)
    sale = await service.create_sale(ctx["store_id"], ctx["clerk_id"], lines=lines)

    assert quote.total == sale.total == Decimal(1600)
    assert [ql.net_amount for ql in quote.lines] == [Decimal(800), Decimal(480), Decimal(320)]
    assert [ql.free_units for ql in quote.lines] == [0, 0, 1]
    allocations = await _allocations(db_session, sale.id)
    assert [allocations[i.id] for i in items] == [
        [(campaign, Decimal(200))],
        [(campaign, Decimal(120))],
        [(campaign, Decimal(80))],
    ]
    saved = (
        await db_session.scalars(
            select(SaleLine).where(SaleLine.sale_id == sale.id).order_by(SaleLine.id)
        )
    ).all()
    assert [(s.unit_price, s.line_total, s.discount_amount) for s in saved] == [
        (Decimal(800), Decimal(800), Decimal(200)),
        (Decimal(480), Decimal(480), Decimal(120)),
        (Decimal(320), Decimal(320), Decimal(80)),
    ]
    assert all(s.campaign_id == campaign for s in saved)


async def test_buy_n_get_m_within_one_catalog_line(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """同一行 3 罐 100 元買二送一：本行 200；各件分攤不整除，單價存平均（67）、小計才是準的。"""
    campaign = await _bngm(db_session, ctx, 2, 1)
    product = CatalogProduct(
        store_id=ctx["store_id"],
        sku="GAS-1",
        name="瓦斯罐",
        unit_price=Decimal(100),
        quantity_on_hand=10,
    )
    db_session.add(product)
    await db_session.flush()
    line = SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product.id, qty=3)

    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[line]
    )

    assert sale.total == Decimal(200)
    saved = await db_session.scalar(select(SaleLine).where(SaleLine.sale_id == sale.id))
    assert saved is not None
    assert (saved.unit_price, saved.line_total, saved.net_amount) == (
        Decimal(67),
        Decimal(200),
        Decimal(200),
    )
    assert (saved.original_unit_price, saved.discount_amount) == (Decimal(100), Decimal(100))
    assert saved.campaign_id == campaign


async def test_returning_one_item_refunds_its_allocated_price(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """退一件退它分攤後的實付，不回推剩下各件（docs/40 §8）。"""
    await _bngm(db_session, ctx, 2, 1)
    items = [await _item(db_session, ctx["store_id"], p) for p in ("1000", "600", "400")]
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[_line(i) for i in items]
    )
    line_id = await db_session.scalar(
        select(SaleLine.id).where(
            SaleLine.sale_id == sale.id, SaleLine.serialized_item_id == items[2].id
        )
    )
    assert line_id is not None

    result = await ReturnsService(db_session).create_return(
        ctx["store_id"],
        sale_id=sale.id,
        lines=[ReturnLineInput(sale_line_id=line_id, qty=1)],
        reason="不要了",
        actor_user_id=ctx["clerk_id"],
        idempotency_key="bngm-return-1",
    )
    assert result.refund_amount == Decimal(320)


async def test_campaign_report_credits_buy_n_get_m(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    campaign = await _bngm(db_session, ctx, 2, 1)
    items = [await _item(db_session, ctx["store_id"], p) for p in ("1000", "600", "400")]
    await SalesService(db_session).create_sale(
        ctx["store_id"], ctx["clerk_id"], lines=[_line(i) for i in items]
    )

    row = next(
        r
        for r in (await ReportsService(db_session).campaign_performance(ctx["store_id"])).rows
        if r.campaign_id == campaign
    )
    assert row.gross_turnover == Decimal(1600)
    assert row.transaction_count == 1
    assert (row.kind, row.buy_qty, row.free_qty) == ("BUY_N_GET_M", 2, 1)


async def test_buy_n_get_m_with_a_zero_share_line_still_checks_out(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """1000 + 10 買一送一：10 元那件分到 0 元折讓——報價能過、結帳也要能過（Codex 審查）。"""
    campaign = await _bngm(db_session, ctx, 1, 1)
    items = [await _item(db_session, ctx["store_id"], p) for p in ("1000", "10")]
    lines = [_line(i) for i in items]
    service = SalesService(db_session)

    quote = await service.quote_sale(ctx["store_id"], lines=lines)
    sale = await service.create_sale(ctx["store_id"], ctx["clerk_id"], lines=lines)

    assert quote.total == sale.total == Decimal(1000)
    assert await _allocations(db_session, sale.id) == {items[0].id: [(campaign, Decimal(10))]}


async def test_clerk_chosen_free_item_at_checkout_is_audited(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """店員指定送 600 那件（裁示 4）：報價與結帳同價、寫稽核（裁示 8：不需核准）。"""
    campaign = await _bngm(db_session, ctx, 2, 1)
    items = [await _item(db_session, ctx["store_id"], p) for p in ("1000", "600", "400")]
    lines = [_line(items[0]), replace(_line(items[1]), promo_free=True), _line(items[2])]
    service = SalesService(db_session)

    quote = await service.quote_sale(ctx["store_id"], lines=lines)
    sale = await service.create_sale(ctx["store_id"], ctx["clerk_id"], lines=lines)

    assert quote.total == sale.total == Decimal(1400)
    assert [ql.free_units for ql in quote.lines] == [0, 1, 0]
    assert [ql.buy_n_get_m_units for ql in quote.lines] == [1, 1, 1]
    audits = (
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "SALE_BNGM_FREE_ITEM_CHOSEN",
                AuditLog.entity_id == str(sale.id),
            )
        )
    ).all()
    assert len(audits) == 1
    assert audits[0].after == {"campaign_id": campaign, "line_index": 1}


async def test_choice_that_does_not_take_effect_is_not_audited(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    await _bngm(db_session, ctx, 2, 1)
    items = [await _item(db_session, ctx["store_id"], p) for p in ("1000", "600")]
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[_line(items[0]), replace(_line(items[1]), promo_free=True)],
    )
    assert sale.total == Decimal(1600)
    count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "SALE_BNGM_FREE_ITEM_CHOSEN", AuditLog.entity_id == str(sale.id))
    )
    assert count == 0


async def test_quote_api_accepts_promo_free_and_reports_groups(
    ctx: dict[str, int], db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await _bngm(db_session, ctx, 2, 1)
    items = [await _item(db_session, ctx["store_id"], p) for p in ("1000", "600", "400")]
    token = encode_access_token(user_id=ctx["clerk_id"], role="CLERK", store_id=ctx["store_id"])

    resp = await client.post(
        "/api/v1/sales/quote",
        json={
            "lines": [
                {"line_type": "SERIALIZED", "item_code": items[0].item_code},
                {"line_type": "SERIALIZED", "item_code": items[1].item_code, "promo_free": True},
                {"line_type": "SERIALIZED", "item_code": items[2].item_code},
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == "1400"
    assert [ln["free_units"] for ln in body["lines"]] == [0, 1, 0]
    assert [ln["buy_n_get_m_units"] for ln in body["lines"]] == [1, 1, 1]
    assert all(ln["buy_n_get_m_eligible"] for ln in body["lines"])
