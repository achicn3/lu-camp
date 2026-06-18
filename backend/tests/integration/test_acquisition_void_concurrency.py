"""收購作廢併發（F6.5）：兩個並行 void 只一個成功、退款/稽核恰一筆。

真並行（asyncio.gather）兩個獨立交易的 void 請求（不覆寫 get_session）。void_acquisition 先以
SELECT … FOR UPDATE 鎖收購列＋刷新已提交狀態（比照 D-1/sales void），故只一個設 voided_at 成功、
另一個鎖後見已作廢 → AcquisitionAlreadyVoid → 409；ACQUISITION_VOID_IN 退款與 VOID_ACQUISITION
稽核皆恰一筆（不雙重沖回）。
"""

import asyncio
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import delete, func, select

import app.core.db as app_db
from app.core.audit import AuditLog
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
from app.modules.acquisition.service import AcquisitionService
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import SerializedItem, StockMovement
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import AcquisitionType, CashMovementType, Grade, UserRole


@pytest_asyncio.fixture
async def real_client() -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_concurrent_void_only_one_succeeds(real_client: httpx.AsyncClient) -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發作廢收購店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="acqv-clk", password_hash="h", role=UserRole.CLERK)
        mgr = User(store_id=store.id, username="acqv-mgr", password_hash="h", role=UserRole.MANAGER)
        seller = Contact(store_id=store.id, name="賣方", roles=["SELLER"], national_id_enc="enc")
        s.add_all([clerk, mgr, seller])
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("5000"))
        result = await AcquisitionService(s).create_acquisition(
            store.id,
            clerk.id,
            AcquisitionCreate(
                type=AcquisitionType.BUYOUT,
                contact_id=seller.id,
                items=[
                    AcquisitionItemIn(
                        name="帳篷",
                        grade=Grade.A,
                        listed_price=Decimal("1800"),
                        acquisition_cost=Decimal("1000"),
                    )
                ],
            ),
            idempotency_key="acqv-create",
        )
        store_id, acq_id = store.id, result.acquisition_id
        token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
        await s.commit()

    headers = {"Authorization": f"Bearer {token}"}
    try:
        url = f"/api/v1/acquisitions/{acq_id}/void"
        r1, r2 = await asyncio.gather(
            real_client.post(url, json={"reason": "x"}, headers=headers),
            real_client.post(url, json={"reason": "x"}, headers=headers),
        )
        assert sorted([r1.status_code, r2.status_code]) == [200, 409]  # 恰一成功、一被擋

        async with sm() as s:
            acq = await s.get(Acquisition, acq_id)
            assert acq is not None and acq.voided_at is not None
            void_in_count = await s.scalar(
                select(func.count())
                .select_from(CashMovement)
                .where(
                    CashMovement.store_id == store_id,
                    CashMovement.type == CashMovementType.ACQUISITION_VOID_IN,
                )
            )
            assert void_in_count == 1  # 退款恰一筆（不雙重沖回）
            audit_count = await s.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.store_id == store_id, AuditLog.action == "VOID_ACQUISITION")
            )
            assert audit_count == 1  # 稽核恰一筆
    finally:
        async with sm() as s:
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
            await s.execute(delete(SerializedItem).where(SerializedItem.store_id == store_id))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(Acquisition).where(Acquisition.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
