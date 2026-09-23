"""門市活動 v2 migration 的舊單回填（docs/40 P1a）。

活動成效報表改讀 sale_line_campaigns 之後，升級前的舊單必須還在：回填把每行的
campaign_id＋discount_amount 轉成一筆明細。可重跑：已有明細的行不會重複寫。
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import SerializedItem
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import SaleLine, SaleLineCampaign
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, SaleLineType, SerializedItemStatus, UserRole


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "a3d7e1f9c2b4_campaigns_v2_multi_stack_targets.py"
    )
    spec = importlib.util.spec_from_file_location("campaigns_v2_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_backfill_restores_old_style_sale_lines_and_is_rerunnable(
    db_session: AsyncSession,
) -> None:
    store = Store(name="回填測試店")
    db_session.add(store)
    await db_session.flush()
    clerk = User(
        store_id=store.id, username=f"bf{store.id}", password_hash="h", role=UserRole.CLERK
    )
    db_session.add(clerk)
    await db_session.flush()
    await CashDrawerService(db_session).open_session(store.id, clerk.id, Decimal(1000))
    now = datetime.now(UTC)
    svc = CampaignService(db_session)
    campaign = await svc.create_campaign(
        store.id,
        name="舊活動",
        discount_pct=10,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=1),
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=False,
        applies_consignment=False,
        created_by=clerk.id,
    )
    await svc.activate(store.id, campaign.id, actor_user_id=clerk.id)
    item = SerializedItem(
        store_id=store.id,
        item_code="BF-1",
        name="舊單商品",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        acquisition_cost=Decimal(100),
        listed_price=Decimal(1000),
        status=SerializedItemStatus.IN_STOCK,
    )
    db_session.add(item)
    await db_session.flush()
    sale = await SalesService(db_session).create_sale(
        store.id,
        clerk.id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="BF-1")],
    )
    line = await db_session.scalar(select(SaleLine).where(SaleLine.sale_id == sale.id))
    assert line is not None
    # 模擬升級前的舊單：只有 sale_lines.campaign_id，沒有明細。
    await db_session.execute(
        delete(SaleLineCampaign).where(SaleLineCampaign.sale_line_id == line.id)
    )

    conn = await db_session.connection()
    migration = _migration()
    await conn.run_sync(migration.backfill_sale_line_campaigns)
    await conn.run_sync(migration.backfill_sale_line_campaigns)  # 重跑不重複

    rows = (
        await db_session.execute(
            select(SaleLineCampaign.campaign_id, SaleLineCampaign.discount_amount).where(
                SaleLineCampaign.sale_line_id == line.id
            )
        )
    ).all()
    assert [(r.campaign_id, Decimal(r.discount_amount)) for r in rows] == [
        (campaign.id, Decimal(100))
    ]
