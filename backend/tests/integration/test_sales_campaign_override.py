"""「這筆不套用」（docs/40 P1c；裁示：不需主管核准、原因可不填）。

店員在某一筆取消某個活動：報價與結帳都不套它、報價回報「本筆未套用」的活動（可恢復）；
成交時記下取消了哪些活動與原因（sale_campaign_overrides）並寫稽核（不含自由文字原因）。
取消的活動納入冪等指紋——同鍵但取消內容不同是不同的請求；沒帶時指紋維持舊形狀。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.customerdisplay.schemas import CartUpsertRequest, StaffCartPayloadRead
from app.modules.customerdisplay.service import CustomerDisplayService
from app.modules.inventory.models import SerializedItem
from app.modules.sales.inputs import CampaignOverrideInput, SaleLineInput, TenderInput
from app.modules.sales.models import SaleCampaignOverride
from app.modules.sales.service import SalesService, _cart_fingerprint
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    Grade,
    OwnershipType,
    SaleLineType,
    SerializedItemStatus,
    TenderType,
    UserRole,
)
from app.shared.exceptions import IdempotencyKeyConflict
from tests.integration.customer_display_helpers import ensure_paired_customer_display

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


async def _campaign(db: AsyncSession, ctx: dict[str, int], pct: int, name: str) -> int:
    now = datetime.now(UTC)
    svc = CampaignService(db)
    c = await svc.create_campaign(
        ctx["store_id"],
        name=name,
        discount_pct=pct,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=1),
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=False,
        applies_consignment=False,
        created_by=ctx["clerk_id"],
        stackable=True,
    )
    await svc.activate(ctx["store_id"], c.id, actor_user_id=ctx["clerk_id"])
    return c.id


async def _item(db: AsyncSession, store_id: int) -> SaleLineInput:
    code = f"OV-{next(_SEQ)}"
    db.add(
        SerializedItem(
            store_id=store_id,
            item_code=code,
            name="序號品",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            acquisition_cost=Decimal(100),
            listed_price=Decimal(1000),
            status=SerializedItemStatus.IN_STOCK,
        )
    )
    await db.flush()
    return SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)


async def test_quote_without_the_disabled_campaign_and_lists_it(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    storewide = await _campaign(db_session, ctx, 10, "全館九折")
    member = await _campaign(db_session, ctx, 10, "會員九折")
    line = await _item(db_session, ctx["store_id"])

    quote = await SalesService(db_session).quote_sale(
        ctx["store_id"],
        lines=[line],
        disabled_campaigns=[CampaignOverrideInput(campaign_id=member, reason="客人不要")],
    )
    assert quote.total == Decimal(900)
    assert [c.campaign_id for c in quote.campaigns] == [storewide]
    assert [(c.campaign_id, c.name) for c in quote.disabled_campaigns] == [(member, "會員九折")]


async def test_checkout_records_the_override_and_audits_it(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    await _campaign(db_session, ctx, 10, "全館九折")
    member = await _campaign(db_session, ctx, 10, "會員九折")
    line = await _item(db_session, ctx["store_id"])

    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[line],
        disabled_campaigns=[CampaignOverrideInput(campaign_id=member, reason="商品已另外議價")],
    )
    assert sale.total == Decimal(900)
    rows = (
        await db_session.scalars(
            select(SaleCampaignOverride).where(SaleCampaignOverride.sale_id == sale.id)
        )
    ).all()
    assert [(r.campaign_id, r.reason) for r in rows] == [(member, "商品已另外議價")]
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "SALE_CAMPAIGN_NOT_APPLIED", AuditLog.entity_id == str(sale.id)
        )
    )
    assert audit is not None
    assert "商品已另外議價" not in str(audit.after)  # 自由文字原因只存在表裡，不進稽核


async def test_disabling_a_campaign_that_is_not_running_is_ignored(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    await _campaign(db_session, ctx, 10, "全館九折")
    line = await _item(db_session, ctx["store_id"])
    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[line],
        disabled_campaigns=[CampaignOverrideInput(campaign_id=999_999, reason=None)],
    )
    assert sale.total == Decimal(900)
    assert (
        await db_session.scalar(
            select(SaleCampaignOverride).where(SaleCampaignOverride.sale_id == sale.id)
        )
    ) is None


def test_fingerprint_keeps_legacy_shape_and_counts_disabled_campaigns() -> None:
    lines = [SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="X-1")]
    legacy = _cart_fingerprint(lines, None)
    assert _cart_fingerprint(lines, None, disabled_campaigns=[]) == legacy
    with_off = _cart_fingerprint(
        lines, None, disabled_campaigns=[CampaignOverrideInput(campaign_id=3, reason="a")]
    )
    assert with_off != legacy
    # 原因不影響金額，不進指紋；順序也不影響
    assert with_off == _cart_fingerprint(
        lines, None, disabled_campaigns=[CampaignOverrideInput(campaign_id=3, reason="b")]
    )


async def test_same_key_with_different_disabled_campaigns_is_a_conflict(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    member = await _campaign(db_session, ctx, 10, "會員九折")
    line = await _item(db_session, ctx["store_id"])
    svc = SalesService(db_session)
    await svc.create_sale(ctx["store_id"], ctx["clerk_id"], lines=[line], idempotency_key="ov-k1")
    with pytest.raises(IdempotencyKeyConflict):
        await svc.create_sale(
            ctx["store_id"],
            ctx["clerk_id"],
            lines=[line],
            idempotency_key="ov-k1",
            disabled_campaigns=[CampaignOverrideInput(campaign_id=member, reason=None)],
        )


async def test_customer_display_cart_keeps_the_override_and_checkout_matches(
    ctx: dict[str, int], db_session: AsyncSession
) -> None:
    """客顯權威購物車帶著「不套用」：快照金額、POS 還原內容、實際結帳三者一致。"""
    await _campaign(db_session, ctx, 10, "全館九折")
    member = await _campaign(db_session, ctx, 10, "會員九折")
    line = await _item(db_session, ctx["store_id"])
    terminal, _device = await ensure_paired_customer_display(
        db_session, store_id=ctx["store_id"], actor_user_id=ctx["clerk_id"]
    )
    request = CartUpsertRequest.model_validate(
        {
            "expected_revision": None,
            "lines": [{"line_type": "SERIALIZED", "item_code": line.item_code}],
            "disabled_campaigns": [{"campaign_id": member, "reason": "客人不要"}],
            "tenders": [{"tender_type": "CASH", "amount": "900"}],
        }
    )
    cart = await CustomerDisplayService(db_session).upsert_cart(
        ctx["store_id"], terminal.id, request, actor_user_id=ctx["clerk_id"]
    )
    assert cart.snapshot["total"] == "900"
    restored = StaffCartPayloadRead.model_validate(cart.staff_payload)
    assert [(d.campaign_id, d.reason) for d in restored.disabled_campaigns] == [
        (member, "客人不要")
    ]

    sale = await SalesService(db_session).create_sale(
        ctx["store_id"],
        ctx["clerk_id"],
        lines=[line],
        cart_session_id=cart.id,
        cart_revision=cart.revision,
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(900))],
        disabled_campaigns=[CampaignOverrideInput(campaign_id=member, reason="客人不要")],
    )
    assert sale.total == Decimal(900)


def test_old_carts_without_the_field_still_restore() -> None:
    payload = StaffCartPayloadRead.model_validate(
        {
            "lines": [
                {"line_type": "SERIALIZED", "item_code": "X", "qty": 1, "line_kind": "NORMAL"}
            ],
            "adjustments": None,
        }
    )
    assert payload.disabled_campaigns == []
