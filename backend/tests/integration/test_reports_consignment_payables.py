"""R4 寄售應付報表整合測試（docs/19 §2.5）：

只計 PENDING 待付；PAID/CANCELLED 分欄；reclaim_needed 獨立不沖抵 pending；status 篩選只影響明細、
合計恆全狀態；禁輸出 national_id；跨店隔離；唯讀；MANAGER；匯出。
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
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.contacts.models import Contact
from app.modules.inventory.models import SerializedItem
from app.modules.sales.models import Sale
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    ConsignmentSettlementStatus,
    Grade,
    OwnershipType,
    SerializedItemStatus,
    UserRole,
)

_SEQ = 0


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


async def _seed(session: AsyncSession) -> tuple[str, int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([mgr, clerk])
    await session.flush()
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        store.id,
        clerk.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _add_consignor(
    session: AsyncSession, store_id: int, *, name: str, phone: str, national_id_enc: str
) -> int:
    c = Contact(
        store_id=store_id,
        name=name,
        phone=phone,
        roles=["SELLER"],
        national_id_enc=national_id_enc,
    )
    session.add(c)
    await session.flush()
    return c.id


async def _add_settlement(
    session: AsyncSession,
    store_id: int,
    clerk_id: int,
    consignor_id: int,
    *,
    status: ConsignmentSettlementStatus,
    gross: str,
    commission: str,
    payout: str,
    reclaim: bool = False,
) -> None:
    global _SEQ
    _SEQ += 1
    item = SerializedItem(
        store_id=store_id,
        item_code=f"CP-{_SEQ}",
        name="寄售品",
        grade=Grade.A,
        ownership_type=OwnershipType.CONSIGNMENT,
        consignor_id=consignor_id,
        commission_pct=50,
        listed_price=Decimal(gross),
        status=SerializedItemStatus.SOLD,
    )
    session.add(item)
    await session.flush()
    sale = Sale(
        store_id=store_id,
        clerk_user_id=clerk_id,
        subtotal=Decimal(gross),
        tax=Decimal(0),
        total=Decimal(gross),
    )
    session.add(sale)
    await session.flush()
    session.add(
        ConsignmentSettlement(
            store_id=store_id,
            serialized_item_id=item.id,
            sale_id=sale.id,
            gross=Decimal(gross),
            commission_pct=50,
            commission_amount=Decimal(commission),
            payout_amount=Decimal(payout),
            status=status,
            reclaim_needed=reclaim,
        )
    )
    await session.flush()


async def _report(client: httpx.AsyncClient, mgr: str, *, status: str = "ALL") -> dict[str, object]:
    resp = await client.get(
        "/api/v1/reports/consignment-payables", params={"status": status}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


async def test_only_pending_counted_in_payable_total(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, clerk_id = await _seed(db_session)
    consignor = await _add_consignor(
        db_session, store_id, name="甲", phone="0911", national_id_enc="e"
    )
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        consignor,
        status=ConsignmentSettlementStatus.PENDING,
        gross="1000",
        commission="500",
        payout="500",
    )
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        consignor,
        status=ConsignmentSettlementStatus.PAID,
        gross="600",
        commission="300",
        payout="300",
    )
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        consignor,
        status=ConsignmentSettlementStatus.CANCELLED,
        gross="400",
        commission="200",
        payout="200",
    )
    body = await _report(client, mgr)
    assert body["total_pending_payout"] == "500"  # 只計 PENDING
    assert body["total_paid_payout"] == "300"
    assert body["total_cancelled_payout"] == "200"
    assert body["total_reclaim_needed_payout"] == "0"


async def test_status_filter_affects_rows_not_totals(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, clerk_id = await _seed(db_session)
    consignor = await _add_consignor(
        db_session, store_id, name="甲", phone="0911", national_id_enc="e"
    )
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        consignor,
        status=ConsignmentSettlementStatus.PENDING,
        gross="1000",
        commission="500",
        payout="500",
    )
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        consignor,
        status=ConsignmentSettlementStatus.PAID,
        gross="600",
        commission="300",
        payout="300",
    )
    pending_only = await _report(client, mgr, status="PENDING")
    p_rows = pending_only["rows"]
    assert isinstance(p_rows, list)
    assert len(p_rows) == 1
    assert p_rows[0]["status"] == "PENDING"
    # 合計仍涵蓋全部狀態
    assert pending_only["total_paid_payout"] == "300"
    all_rows = await _report(client, mgr, status="ALL")
    a_rows = all_rows["rows"]
    assert isinstance(a_rows, list)
    assert len(a_rows) == 2


async def test_reclaim_needed_separate_not_offsetting_pending(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已付後退貨 reclaim_needed：獨立分欄，不以負數沖抵 pending。"""
    mgr, store_id, clerk_id = await _seed(db_session)
    consignor = await _add_consignor(
        db_session, store_id, name="甲", phone="0911", national_id_enc="e"
    )
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        consignor,
        status=ConsignmentSettlementStatus.PENDING,
        gross="1000",
        commission="500",
        payout="500",
    )
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        consignor,
        status=ConsignmentSettlementStatus.PAID,
        gross="600",
        commission="300",
        payout="300",
        reclaim=True,
    )
    body = await _report(client, mgr)
    assert body["total_pending_payout"] == "500"  # 未被 reclaim 沖抵
    assert body["total_reclaim_needed_payout"] == "300"
    assert body["total_paid_payout"] == "300"


async def test_no_national_id_in_output(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, clerk_id = await _seed(db_session)
    consignor = await _add_consignor(
        db_session, store_id, name="王先生", phone="0912345678", national_id_enc="SECRET_ENC_BLOB"
    )
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        consignor,
        status=ConsignmentSettlementStatus.PENDING,
        gross="1000",
        commission="500",
        payout="500",
    )
    resp = await client.get(
        "/api/v1/reports/consignment-payables", params={"status": "ALL"}, headers=_auth(mgr)
    )
    assert "SECRET_ENC_BLOB" not in resp.text
    row = resp.json()["rows"][0]
    assert row["consignor_name"] == "王先生"
    assert row["consignor_phone"] == "0912345678"
    assert "national_id" not in row


async def test_consignment_payables_cross_store_isolation(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, clerk_id = await _seed(db_session)
    other = Store(name="他店")
    db_session.add(other)
    await db_session.flush()
    other_clerk = User(store_id=other.id, username="oc", password_hash="h", role=UserRole.CLERK)
    db_session.add(other_clerk)
    await db_session.flush()
    c1 = await _add_consignor(db_session, store_id, name="甲", phone="0911", national_id_enc="e")
    c2 = await _add_consignor(db_session, other.id, name="乙", phone="0922", national_id_enc="e")
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        c1,
        status=ConsignmentSettlementStatus.PENDING,
        gross="1000",
        commission="500",
        payout="500",
    )
    await _add_settlement(
        db_session,
        other.id,
        other_clerk.id,
        c2,
        status=ConsignmentSettlementStatus.PENDING,
        gross="9999",
        commission="5000",
        payout="4999",
    )
    body = await _report(client, mgr)
    assert body["total_pending_payout"] == "500"
    rows = body["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1


async def test_consignment_payables_read_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, clerk_id = await _seed(db_session)
    consignor = await _add_consignor(
        db_session, store_id, name="甲", phone="0911", national_id_enc="e"
    )
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        consignor,
        status=ConsignmentSettlementStatus.PENDING,
        gross="1000",
        commission="500",
        payout="500",
    )
    before = await db_session.scalar(select(func.count()).select_from(ConsignmentSettlement))
    await _report(client, mgr)
    after = await db_session.scalar(select(func.count()).select_from(ConsignmentSettlement))
    assert before == after


async def test_consignment_payables_manager_only_and_csv(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, clerk_id = await _seed(db_session)
    clerk_token = encode_access_token(user_id=clerk_id, role="CLERK", store_id=store_id)
    consignor = await _add_consignor(
        db_session, store_id, name="甲", phone="0911", national_id_enc="e"
    )
    await _add_settlement(
        db_session,
        store_id,
        clerk_id,
        consignor,
        status=ConsignmentSettlementStatus.PENDING,
        gross="1000",
        commission="500",
        payout="500",
    )
    forbidden = await client.get("/api/v1/reports/consignment-payables", headers=_auth(clerk_token))
    assert forbidden.status_code == 403
    csv_resp = await client.get(
        "/api/v1/reports/consignment-payables",
        params={"format": "csv"},
        headers=_auth(mgr),
    )
    assert csv_resp.status_code == 200
    text = csv_resp.content.decode("utf-8-sig")
    assert "寄售人" in text and "應付" in text and "待付合計(PENDING)" in text
