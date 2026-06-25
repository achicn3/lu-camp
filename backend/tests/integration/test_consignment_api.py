"""Phase 4 / Slice 4A — consignment 付款 API：list + pay 的 HTTP 行為與錯誤碼。

併發（兩個 pay 只一筆成功）另見 test_consignment_payout_concurrency.py。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.contacts.models import Contact
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
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


def _auth(token: str, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


async def _seed(db_session: AsyncSession) -> tuple[str, int, int]:
    """建 store+clerk+開帳 → 寄售品現金售出 → 回 (clerk_token, settlement_id, store_id)。"""
    store = Store(name="寄售付款 API 店")
    db_session.add(store)
    await db_session.flush()
    clerk = User(store_id=store.id, username="cpa-clk", password_hash="h", role=UserRole.CLERK)
    db_session.add(clerk)
    await db_session.flush()
    await CashDrawerService(db_session).open_session(store.id, clerk.id, Decimal("1000"))
    consignor = Contact(
        store_id=store.id, name="寄售人", phone="0912-000-001", national_id_enc="enc"
    )
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
    r = await client.post(
        f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token, "pay-once")
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == ConsignmentSettlementStatus.PAID.value
    assert body["payout_amount"] == _PAYOUT
    assert body["paid_at"] is not None


async def test_pay_replay_same_idempotency_key_returns_paid_result(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, sid, store_id = await _seed(db_session)
    url = f"/api/v1/consignment/settlements/{sid}/pay"
    headers = _auth(token, "pay-retry")

    first = await client.post(url, headers=headers)
    assert first.status_code == 200, first.text
    replay = await client.post(url, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"] == sid
    assert replay.json()["status"] == ConsignmentSettlementStatus.PAID.value

    payout_count = await db_session.scalar(
        select(func.count())
        .select_from(CashMovement)
        .where(
            CashMovement.store_id == store_id,
            CashMovement.type == CashMovementType.CONSIGNMENT_PAYOUT_OUT,
        )
    )
    assert payout_count == 1


async def test_pay_already_paid_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, sid, _ = await _seed(db_session)
    first = await client.post(
        f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token, "pay-first")
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token, "pay-second")
    )
    assert second.status_code == 409, second.text


async def test_pay_requires_idempotency_key(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, sid, _ = await _seed(db_session)
    r = await client.post(f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token))
    assert r.status_code == 422, r.text


async def test_pay_without_open_session_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, sid, store_id = await _seed(db_session)
    drawer = CashDrawerService(db_session)
    cs = await drawer.get_current_session(store_id)
    assert cs is not None
    await drawer.close_session(cs, await drawer.expected_amount(cs), cs.opened_by)
    r = await client.post(
        f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token, "pay-no-drawer")
    )
    assert r.status_code == 409, r.text


async def test_pay_unknown_settlement_returns_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _sid, _ = await _seed(db_session)
    r = await client.post(
        "/api/v1/consignment/settlements/999999/pay", headers=_auth(token, "pay-missing")
    )
    assert r.status_code == 404, r.text


async def test_list_settlements_filters_by_status(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, sid, _ = await _seed(db_session)
    pending = await client.get(
        "/api/v1/consignment/settlements", params={"status": "PENDING"}, headers=_auth(token)
    )
    assert pending.status_code == 200
    row = next(row for row in pending.json() if row["id"] == sid)
    assert row["consignor_name"] == "寄售人"
    assert row["consignor_phone"] == "0912-000-001"
    assert row["item_code"] == "C1"
    assert row["item_name"] == "寄售帳篷"
    assert row["sale_created_at"] is not None

    await client.post(
        f"/api/v1/consignment/settlements/{sid}/pay", headers=_auth(token, "pay-list")
    )
    paid = await client.get(
        "/api/v1/consignment/settlements", params={"status": "PAID"}, headers=_auth(token)
    )
    assert paid.status_code == 200
    assert any(row["id"] == sid for row in paid.json())
    still_pending = await client.get(
        "/api/v1/consignment/settlements", params={"status": "PENDING"}, headers=_auth(token)
    )
    assert all(row["id"] != sid for row in still_pending.json())


async def test_list_settlements_filters_by_consignor_phone(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """以寄售人手機（部分）找出其結算；不符的手機回空清單。"""
    token, sid, _ = await _seed(db_session)

    hit = await client.get(
        "/api/v1/consignment/settlements", params={"phone": "0912-000-001"}, headers=_auth(token)
    )
    assert hit.status_code == 200, hit.text
    assert any(row["id"] == sid for row in hit.json())

    partial = await client.get(
        "/api/v1/consignment/settlements", params={"phone": "000-001"}, headers=_auth(token)
    )
    assert partial.status_code == 200, partial.text
    assert any(row["id"] == sid for row in partial.json())

    miss = await client.get(
        "/api/v1/consignment/settlements", params={"phone": "0900-999-999"}, headers=_auth(token)
    )
    assert miss.status_code == 200, miss.text
    assert all(row["id"] != sid for row in miss.json())
