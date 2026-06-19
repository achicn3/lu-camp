"""寄售付款併發（Phase 4 / 4A）：兩個並行 pay 只一筆成功、現金出帳與稽核恰一筆。

真並行（asyncio.gather）兩個獨立交易的 pay 請求（不覆寫 get_session）。pay_settlement 先以
SELECT … FOR UPDATE 鎖結算列＋讀已提交狀態（比照 D-1/F6.5 void），故只一個設 PAID 成功、
另一個鎖後見非 PENDING → SettlementNotPending → 409；CONSIGNMENT_PAYOUT_OUT 出帳與
CONSIGNMENT_PAYOUT 稽核皆恰一筆（不重複出帳）。
"""

import asyncio
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

import app.core.db as app_db
from app.core.audit import AuditLog
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.consignment.service import ConsignmentService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import SerializedItem, StockMovement
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import Sale, SaleLine, SaleTender
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    CashMovementType,
    ConsignmentSettlementStatus,
    Grade,
    OwnershipType,
    SaleLineType,
    UserRole,
)
from app.shared.exceptions import SettlementNotPending


async def _seed_committed(sm: object) -> tuple[int, int, int]:
    """以真實 session 建好一筆現金售出的 PENDING 寄售結算並提交；回 (store_id, sid, clerk_id)。"""
    async with sm() as s:  # type: ignore[operator]
        store = Store(name="寄售付款舊狀態店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="cps-clk", password_hash="h", role=UserRole.CLERK)
        consignor = Contact(store_id=store.id, name="寄售人", national_id_enc="enc")
        s.add_all([clerk, consignor])
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("1000"))
        await InventoryService(s).create_serialized_item(
            store.id,
            item_code="CPS1",
            name="寄售帳篷",
            grade=Grade.A,
            ownership_type=OwnershipType.CONSIGNMENT,
            listed_price=Decimal("1800"),
            consignor_id=consignor.id,
            commission_pct=40,
        )
        sale = await SalesService(s).create_sale(
            store.id,
            clerk.id,
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="CPS1")],
        )
        settlement = await s.scalar(
            select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale.id)
        )
        assert settlement is not None
        ids = (store.id, settlement.id, clerk.id)
        await s.commit()
    return ids


async def _cleanup(sm: object, store_id: int) -> None:
    async with sm() as s:  # type: ignore[operator]
        await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
        await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
        await s.execute(
            delete(ConsignmentSettlement).where(ConsignmentSettlement.store_id == store_id)
        )
        await s.execute(delete(SaleTender).where(SaleTender.store_id == store_id))
        await s.execute(delete(SaleLine).where(SaleLine.store_id == store_id))
        await s.execute(delete(Sale).where(Sale.store_id == store_id))
        await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
        await s.execute(delete(SerializedItem).where(SerializedItem.store_id == store_id))
        await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
        await s.execute(delete(Contact).where(Contact.store_id == store_id))
        await s.execute(delete(User).where(User.store_id == store_id))
        await s.execute(delete(Store).where(Store.id == store_id))
        await s.commit()


@pytest_asyncio.fixture
async def real_client() -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_concurrent_pay_only_one_succeeds(real_client: httpx.AsyncClient) -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發寄售付款店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="cpc-clk", password_hash="h", role=UserRole.CLERK)
        consignor = Contact(store_id=store.id, name="寄售人", national_id_enc="enc")
        s.add_all([clerk, consignor])
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("1000"))
        await InventoryService(s).create_serialized_item(
            store.id,
            item_code="CPC1",
            name="寄售帳篷",
            grade=Grade.A,
            ownership_type=OwnershipType.CONSIGNMENT,
            listed_price=Decimal("1800"),
            consignor_id=consignor.id,
            commission_pct=40,
        )
        sale = await SalesService(s).create_sale(
            store.id,
            clerk.id,
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="CPC1")],
        )
        settlement = await s.scalar(
            select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale.id)
        )
        assert settlement is not None
        store_id, sid = store.id, settlement.id
        token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
        await s.commit()

    headers = {"Authorization": f"Bearer {token}"}
    try:
        url = f"/api/v1/consignment/settlements/{sid}/pay"
        r1, r2 = await asyncio.gather(
            real_client.post(url, headers=headers),
            real_client.post(url, headers=headers),
        )
        assert sorted([r1.status_code, r2.status_code]) == [200, 409]  # 恰一成功、一被擋

        async with sm() as s:
            settlement = await s.get(ConsignmentSettlement, sid)
            assert settlement is not None
            assert settlement.status == ConsignmentSettlementStatus.PAID
            payout_count = await s.scalar(
                select(func.count())
                .select_from(CashMovement)
                .where(
                    CashMovement.store_id == store_id,
                    CashMovement.type == CashMovementType.CONSIGNMENT_PAYOUT_OUT,
                )
            )
            assert payout_count == 1  # 出帳恰一筆（不重複付款）
            audit_count = await s.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.store_id == store_id, AuditLog.action == "CONSIGNMENT_PAYOUT")
            )
            assert audit_count == 1  # 稽核恰一筆
    finally:
        async with sm() as s:
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(
                delete(ConsignmentSettlement).where(ConsignmentSettlement.store_id == store_id)
            )
            await s.execute(delete(SaleTender).where(SaleTender.store_id == store_id))
            await s.execute(delete(SaleLine).where(SaleLine.store_id == store_id))
            await s.execute(delete(Sale).where(Sale.store_id == store_id))
            await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
            await s.execute(delete(SerializedItem).where(SerializedItem.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_stale_identity_map_does_not_double_pay() -> None:
    """身分映射舊狀態不得重複付款（Codex adversarial high）。

    A session 先載入 PENDING、B session 付款並提交後，A 再付款須因 FOR UPDATE +
    populate_existing 刷新到 PAID → SettlementNotPending，CONSIGNMENT_PAYOUT_OUT 仍恰一筆。
    少了 populate_existing 此測會見到舊 PENDING 而重複出帳。
    """
    sm = app_db.get_sessionmaker()
    store_id, sid, clerk_id = await _seed_committed(sm)
    try:
        async with sm() as session_a, sm() as session_b:
            # A 先把結算載入身分映射（此刻為 PENDING）。
            preloaded = await session_a.get(ConsignmentSettlement, sid)
            assert preloaded is not None
            assert preloaded.status == ConsignmentSettlementStatus.PENDING
            # B 付款並提交（DB 內已 PAID、現金出帳一筆）。
            await ConsignmentService(session_b).pay_settlement(
                store_id, sid, actor_user_id=clerk_id
            )
            await session_b.commit()
            # A 再付款：鎖列＋刷新後應見 PAID → 擋下，不重複出帳。
            with pytest.raises(SettlementNotPending):
                await ConsignmentService(session_a).pay_settlement(
                    store_id, sid, actor_user_id=clerk_id
                )

        async with sm() as s:
            payout_count = await s.scalar(
                select(func.count())
                .select_from(CashMovement)
                .where(
                    CashMovement.store_id == store_id,
                    CashMovement.type == CashMovementType.CONSIGNMENT_PAYOUT_OUT,
                )
            )
            assert payout_count == 1
    finally:
        await _cleanup(sm, store_id)
