"""acquisition 整筆原子性：任一步失敗 → 整筆回復，不留半套（docs/07 Phase 2 驗收）。

用獨立 session（真正的交易、各自 commit）而非 db_session 回滾隔離，並在「庫存已建、
要扣現金」那一步注入失敗，證明 serialized_item / stock_movement / acquisition / cash_movement
全部都沒落地——即不會出現「庫存建了但現金沒扣」的半套。
"""

import itertools
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.db as app_db
from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
from app.modules.acquisition.service import AcquisitionService
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import SerializedItem, StockMovement
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import AcquisitionType, Grade, UserRole

_svc_idem = itertools.count()


async def test_acquisition_rolls_back_entirely_when_cash_step_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="原子店")
        s.add(store)
        await s.flush()
        clerk = User(
            store_id=store.id, username="atomic-clerk", password_hash="h", role=UserRole.CLERK
        )
        contact = Contact(store_id=store.id, name="原子賣家", national_id_enc="enc")
        s.add_all([clerk, contact])
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("1000"))
        store_id, clerk_id, contact_id = store.id, clerk.id, contact.id
        await s.commit()

    payload = AcquisitionCreate(
        type=AcquisitionType.BUYOUT,
        contact_id=contact_id,
        items=[
            AcquisitionItemIn(
                name="相機",
                grade=Grade.A,
                listed_price=Decimal("3000"),
                acquisition_cost=Decimal("1800"),
            )
        ],
    )

    # 注入：庫存建完、要記 BUYOUT_OUT 現金那一步炸掉。
    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("模擬付現步驟失敗")

    monkeypatch.setattr(CashDrawerService, "record_movement", _boom)

    try:
        async with sm() as s:
            with pytest.raises(RuntimeError):
                await AcquisitionService(s).create_acquisition(
                    store_id, clerk_id, payload, idempotency_key=f"svc-{next(_svc_idem)}"
                )
            await s.rollback()

        # 全部都沒落地：acquisition / serialized_item / stock_movement / cash_movement 皆為 0。
        async with sm() as s:
            assert await _count(s, Acquisition, store_id) == 0
            assert await _count(s, SerializedItem, store_id) == 0
            assert await _count(s, StockMovement, store_id) == 0
            # 開帳本身不產生任何 movement，故收購回滾後現金異動仍為 0。
            assert await _count(s, CashMovement, store_id) == 0
    finally:
        async with sm() as s:
            for model in (
                StockMovement,
                CashMovement,
                SerializedItem,
                Acquisition,
                CashSession,
            ):
                await s.execute(delete(model).where(model.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def _count(session: AsyncSession, model: Any, store_id: int) -> int:
    count = await session.scalar(
        select(func.count()).select_from(model).where(model.store_id == store_id)
    )
    return count or 0
