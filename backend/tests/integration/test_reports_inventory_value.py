"""R3 庫存價值與庫齡報表整合測試（docs/19 §2.4）：

自有計成本、寄售另列售價、catalog 成本 N/A；已售/退場/remaining=0 不入；散裝剩餘成本四捨五入；
庫齡按入庫時間分桶（Σ=自有在庫成本）；跨店隔離；唯讀；MANAGER；匯出。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, CatalogProduct, SerializedItem
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
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


async def _add_serialized(
    session: AsyncSession,
    store_id: int,
    *,
    ownership: OwnershipType,
    cost: str | None,
    price: str,
    status: SerializedItemStatus = SerializedItemStatus.IN_STOCK,
    intake: datetime | None = None,
    consignor_id: int | None = None,
) -> None:
    global _SEQ
    _SEQ += 1
    kwargs: dict[str, object] = {}
    if intake is not None:
        kwargs["intake_date"] = intake
    session.add(
        SerializedItem(
            store_id=store_id,
            item_code=f"IV-{_SEQ}",
            name="序號品",
            grade=Grade.A,
            ownership_type=ownership,
            acquisition_cost=None if cost is None else Decimal(cost),
            consignor_id=consignor_id,
            listed_price=Decimal(price),
            status=status,
            **kwargs,
        )
    )
    await session.flush()


async def _add_bulk(
    session: AsyncSession,
    store_id: int,
    *,
    cost: str,
    total: int,
    remaining: int,
    unit_price: str,
    status: BulkLotStatus = BulkLotStatus.ON_SALE,
    consignor_id: int | None = None,
    intake: datetime | None = None,
) -> None:
    global _SEQ
    _SEQ += 1
    kwargs: dict[str, object] = {}
    if intake is not None:
        kwargs["intake_date"] = intake
    session.add(
        BulkLot(
            store_id=store_id,
            lot_code=f"IVL-{_SEQ}",
            name="散裝",
            grade=Grade.E,
            acquisition_cost=Decimal(cost),
            acquisition_basis=BulkAcquisitionBasis.BAG,
            unit_price=Decimal(unit_price),
            total_qty=total,
            remaining_qty=remaining,
            status=status,
            consignor_id=consignor_id,
            **kwargs,
        )
    )
    await session.flush()


async def _add_catalog(session: AsyncSession, store_id: int, *, price: str, qty: int) -> None:
    global _SEQ
    _SEQ += 1
    session.add(
        CatalogProduct(
            store_id=store_id,
            sku=f"IVC-{_SEQ}",
            name="數量品",
            unit_price=Decimal(price),
            quantity_on_hand=qty,
        )
    )
    await session.flush()


async def _report(client: httpx.AsyncClient, mgr: str) -> dict[str, object]:
    resp = await client.get("/api/v1/reports/inventory-value", headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


async def test_owned_serialized_value_and_excludes_sold(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, _clerk = await _seed(db_session)
    await _add_serialized(
        db_session, store_id, ownership=OwnershipType.OWNED, cost="300", price="500"
    )
    # 已售/退場/沖銷不入在庫
    await _add_serialized(
        db_session,
        store_id,
        ownership=OwnershipType.OWNED,
        cost="999",
        price="999",
        status=SerializedItemStatus.SOLD,
    )
    await _add_serialized(
        db_session,
        store_id,
        ownership=OwnershipType.OWNED,
        cost="888",
        price="888",
        status=SerializedItemStatus.WRITTEN_OFF,
    )
    body = await _report(client, mgr)
    assert body["owned_serialized_count"] == 1
    assert body["owned_serialized_cost"] == "300"
    assert body["owned_serialized_retail"] == "500"
    assert body["total_owned_cost_value"] == "300"


async def test_consignment_serialized_listed_separately(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, _clerk = await _seed(db_session)
    consignor = Contact(store_id=store_id, name="寄售人", roles=["SELLER"], national_id_enc="e")
    db_session.add(consignor)
    await db_session.flush()
    await _add_serialized(
        db_session,
        store_id,
        ownership=OwnershipType.CONSIGNMENT,
        cost=None,
        price="1000",
        consignor_id=consignor.id,
    )
    body = await _report(client, mgr)
    assert body["consignment_serialized_count"] == 1
    assert body["consignment_inventory_gross"] == "1000"
    # 不計入自有資產
    assert body["owned_serialized_count"] == 0
    assert body["total_owned_cost_value"] == "0"


async def test_owned_bulk_remaining_cost_rounds_and_excludes_zero(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, _clerk = await _seed(db_session)
    # 剩 4/10、每批成本 1000 → 剩餘成本 round_ntd(1000*4/10)=400；售價 200*4=800
    await _add_bulk(db_session, store_id, cost="1000", total=10, remaining=4, unit_price="200")
    # remaining=0 → 不入（且 bulk_for_valuation 已篩 remaining>0）
    await _add_bulk(
        db_session,
        store_id,
        cost="500",
        total=5,
        remaining=0,
        unit_price="100",
        status=BulkLotStatus.SOLD_OUT,
    )
    body = await _report(client, mgr)
    assert body["owned_bulk_remaining_qty"] == 4
    assert body["owned_bulk_cost"] == "400"
    assert body["owned_bulk_retail"] == "800"
    assert body["total_owned_cost_value"] == "400"


async def test_catalog_cost_is_na(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, store_id, _clerk = await _seed(db_session)
    await _add_catalog(db_session, store_id, price="100", qty=5)
    body = await _report(client, mgr)
    assert body["catalog_total_qty"] == 5
    assert body["catalog_retail_value"] == "500"
    assert body["catalog_cost_value"] is None  # 成本未建模 → N/A


async def test_owned_cost_aging_buckets(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, _clerk = await _seed(db_session)
    now = datetime.now(UTC)
    await _add_serialized(
        db_session,
        store_id,
        ownership=OwnershipType.OWNED,
        cost="100",
        price="100",
        intake=now - timedelta(days=5),
    )
    await _add_serialized(
        db_session,
        store_id,
        ownership=OwnershipType.OWNED,
        cost="200",
        price="200",
        intake=now - timedelta(days=200),
    )
    body = await _report(client, mgr)
    aging = body["owned_cost_aging"]
    assert isinstance(aging, dict)
    assert aging["lt_30d"] == "100"
    assert aging["d180_365"] == "200"
    # Σ 桶 = 自有在庫成本
    assert body["total_owned_cost_value"] == "300"


async def test_inventory_value_cross_store_isolation(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, _clerk = await _seed(db_session)
    other = Store(name="他店")
    db_session.add(other)
    await db_session.flush()
    await _add_serialized(
        db_session, store_id, ownership=OwnershipType.OWNED, cost="300", price="500"
    )
    await _add_serialized(
        db_session, other.id, ownership=OwnershipType.OWNED, cost="9999", price="9999"
    )
    body = await _report(client, mgr)
    assert body["owned_serialized_count"] == 1
    assert body["owned_serialized_cost"] == "300"


async def test_inventory_value_is_read_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, _clerk = await _seed(db_session)
    await _add_serialized(
        db_session, store_id, ownership=OwnershipType.OWNED, cost="300", price="500"
    )
    before = await db_session.scalar(select(func.count()).select_from(SerializedItem))
    await _report(client, mgr)
    after = await db_session.scalar(select(func.count()).select_from(SerializedItem))
    assert before == after


async def test_inventory_value_manager_only_and_csv(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, store_id, clerk_id = await _seed(db_session)
    clerk_token = encode_access_token(user_id=clerk_id, role="CLERK", store_id=store_id)
    await _add_serialized(
        db_session, store_id, ownership=OwnershipType.OWNED, cost="300", price="500"
    )
    forbidden = await client.get("/api/v1/reports/inventory-value", headers=_auth(clerk_token))
    assert forbidden.status_code == 403
    csv_resp = await client.get(
        "/api/v1/reports/inventory-value", params={"format": "csv"}, headers=_auth(mgr)
    )
    assert csv_resp.status_code == 200
    text = csv_resp.content.decode("utf-8-sig")
    assert "自有序號" in text and "成本價值" in text
