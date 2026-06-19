"""Phase 4 / Slice 4A — consignment 付款 API：list + pay 的 HTTP 行為與錯誤碼。

併發（兩個 pay 只一筆成功）另見 test_consignment_payout_concurrency.py。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.contacts.models import Contact
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    ConsignmentSettlementStatus,
    Grade,
    OwnershipType,
    SaleLineType,
    UserRole,
)

_PRICE = Decimal("1800")
_PCT = 40
_PAYOUT = "1080"


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed(db_session: AsyncSession) -> tuple[str, int, int]:
    """建 store+clerk+開帳 → 寄售品現金售出 → 回 (clerk_token, settlement_id, store_id)。"""
    store = Store(name="寄售付款 API 店")
    db_session.add(store)
    await db_session.flush()
    clerk = User(store_id=store.id, username="cpa-clk", password_hash="h", role=UserRole.CLERK)
    db_session.add(clerk)
    await db_session.flush()
    await CashDrawerService(db_session).open_session(store.id, clerk.id, Decimal("1000"))
    consignor = Contact(store_id=store.id, name="寄售人", national_id_enc="enc")
    db_session.add(consignor)
    await db_session.flush()
    await InventoryService(db_session).create_serialized_item(
        store.id,
        item_code="C1",
        name="寄售帳篷",
        grade=Grade.A,
        ownership_type=OwnershipType.CONSIGNMENT,
        listed_price=_PRICE,
        consignor_id=consignor.id,
        commission_pct=_PCT,
    )
    sale = await SalesService(db_session).create_sale(
        store.id,
        clerk.id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="C1")],
    )
    settlement = await db_session.scalar(
        select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale.id)
    )
    assert settlement is not None
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, settlement.id, store.id


async def test_pay_endpoint_marks_paid(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, sid, _ = await _seed(db_session)
    r = await client.post(f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == ConsignmentSettlementStatus.PAID.value
    assert body["payout_amount"] == _PAYOUT
    assert body["paid_at"] is not None


async def test_pay_already_paid_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, sid, _ = await _seed(db_session)
    first = await client.post(f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token))
    assert first.status_code == 200
    second = await client.post(f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token))
    assert second.status_code == 409, second.text


async def test_pay_without_open_session_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, sid, store_id = await _seed(db_session)
    drawer = CashDrawerService(db_session)
    cs = await drawer.get_current_session(store_id)
    assert cs is not None
    await drawer.close_session(cs, await drawer.expected_amount(cs), cs.opened_by)
    r = await client.post(f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token))
    assert r.status_code == 409, r.text


async def test_pay_unknown_settlement_returns_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _sid, _ = await _seed(db_session)
    r = await client.post("/api/v1/consignment/settlements/999999/pay", headers=_auth(token))
    assert r.status_code == 404, r.text


async def test_list_settlements_filters_by_status(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, sid, _ = await _seed(db_session)
    pending = await client.get(
        "/api/v1/consignment/settlements", params={"status": "PENDING"}, headers=_auth(token)
    )
    assert pending.status_code == 200
    assert any(row["id"] == sid for row in pending.json())

    await client.post(f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token))
    paid = await client.get(
        "/api/v1/consignment/settlements", params={"status": "PAID"}, headers=_auth(token)
    )
    assert paid.status_code == 200
    assert any(row["id"] == sid for row in paid.json())
    still_pending = await client.get(
        "/api/v1/consignment/settlements", params={"status": "PENDING"}, headers=_auth(token)
    )
    assert all(row["id"] != sid for row in still_pending.json())
