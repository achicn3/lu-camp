"""Current menu availability is captured durably and sent independently of publication."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.menu.models import MenuItem, MenuOption, MenuOptionGroup
from app.modules.menu.service import today
from app.modules.onlineorder.availability_service import AvailabilityService
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.models import OnlineMenuAvailability, OnlineMenuPublication
from app.modules.onlineorder.signing import canonical_string
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole
from tests.conftest import test_engine


async def _seed(session: AsyncSession) -> tuple[int, int, int]:
    store = Store(name="Availability Test")
    session.add(store)
    await session.flush()
    user = User(
        store_id=store.id, username="availability", password_hash="h", role=UserRole.MANAGER
    )
    session.add(user)
    await session.flush()
    item = MenuItem(
        store_id=store.id,
        name="Cake",
        unit_price=Decimal(90),
        is_available=True,
        daily_limited=True,
        stock_qty=4,
        stock_day=today(),
    )
    session.add(item)
    await session.flush()
    return store.id, user.id, item.id


async def _publish(session: AsyncSession, store_id: int, user_id: int, version: int) -> None:
    session.add(
        OnlineMenuPublication(
            store_id=store_id,
            version=version,
            sha256="a" * 64,
            item_count=1,
            published_by=user_id,
            published_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def test_capture_requires_publication_and_retries_same_revision(
    db_session: AsyncSession,
) -> None:
    store_id, user_id, item_id = await _seed(db_session)
    service = AvailabilityService(db_session)
    assert await service.capture(store_id) is None
    await _publish(db_session, store_id, user_id, 100)

    first = await service.capture(store_id)
    assert first == {
        "menu_version": 100,
        "revision": 1,
        "items": [{"id": item_id, "available": True, "remaining": 4}],
        "options": [],
        "retail": [],
        "popular": [],
    }
    assert await service.capture(store_id) == first  # Lost ACK: exact payload and revision.
    assert await service.mark_delivered(store_id, 100, 1)
    assert await service.capture(store_id) is None

    item = await db_session.get(MenuItem, item_id)
    assert item is not None
    item.stock_qty = 2
    await db_session.flush()
    changed = await service.capture(store_id)
    assert changed is not None
    assert changed["revision"] == 2
    assert changed["items"] == [{"id": item_id, "available": True, "remaining": 2}]
    assert not await service.mark_delivered(store_id, 100, 1)
    row = await db_session.scalar(
        select(OnlineMenuAvailability).where(OnlineMenuAvailability.store_id == store_id)
    )
    assert row is not None and row.delivery_state == "PENDING"
    assert await service.mark_delivered(store_id, 100, 2)
    item.unit_price = Decimal(95)
    await db_session.flush()
    assert await service.capture(store_id) is None  # Price edits require an explicit menu publish.
    item.stock_day = today() - timedelta(days=1)
    await db_session.flush()
    reset = await service.capture(store_id)
    assert reset is not None and reset["items"][0]["remaining"] == 0


async def test_capture_tracks_disabled_archived_options_and_version(
    db_session: AsyncSession,
) -> None:
    store_id, user_id, item_id = await _seed(db_session)
    group = MenuOptionGroup(store_id=store_id, name="Milk", min_select=0, max_select=1)
    db_session.add(group)
    await db_session.flush()
    option = MenuOption(
        store_id=store_id,
        group_id=group.id,
        name="Oat",
        price_delta=Decimal(10),
        is_available=True,
        daily_limited=True,
        stock_qty=3,
        stock_day=today(),
    )
    db_session.add(option)
    await db_session.flush()
    await _publish(db_session, store_id, user_id, 200)
    service = AvailabilityService(db_session)
    first = await service.capture(store_id)
    assert first is not None
    assert first["options"] == [{"id": option.id, "available": True, "remaining": 3}]
    await service.mark_delivered(store_id, 200, 1)

    item = await db_session.get(MenuItem, item_id)
    assert item is not None
    item.is_available = False
    option.is_available = False
    option.stock_qty = 0
    await db_session.flush()
    disabled = await service.capture(store_id)
    assert disabled is not None
    assert disabled["items"] == [{"id": item_id, "available": False, "remaining": 4}]
    assert disabled["options"] == [{"id": option.id, "available": False, "remaining": 0}]
    await service.mark_delivered(store_id, 200, 2)

    item.archived_at = datetime.now(UTC)
    option.archived_at = datetime.now(UTC)
    await db_session.flush()
    archived = await service.capture(store_id)
    assert archived is not None and archived["items"] == [] and archived["options"] == []
    await service.mark_delivered(store_id, 200, 3)
    await _publish(db_session, store_id, user_id, 201)
    newer = await service.capture(store_id)
    assert newer is not None and newer["menu_version"] == 201 and newer["revision"] == 4


async def test_conflict_recaptures_without_price_fields(db_session: AsyncSession) -> None:
    store_id, user_id, _ = await _seed(db_session)
    await _publish(db_session, store_id, user_id, 300)
    service = AvailabilityService(db_session)
    first = await service.capture(store_id)
    assert first is not None
    assert await service.mark_conflict(store_id, 300, 1, "version_conflict")
    second = await service.capture(store_id)
    assert second is not None and second["revision"] == 2
    assert second["items"] == first["items"]
    assert "price" not in str(second)
    assert await service.capture(store_id) == second
    assert await service.mark_delivered(store_id, 300, 2)
    assert not await service.mark_error(store_id, 300, 2, "late timeout")
    assert await service.capture(store_id) is None


async def test_stale_revision_uses_cloud_floor_and_recaptures(db_session: AsyncSession) -> None:
    store_id, user_id, _ = await _seed(db_session)
    await _publish(db_session, store_id, user_id, 400)
    service = AvailabilityService(db_session)
    first = await service.capture(store_id)
    assert first is not None and first["revision"] == 1
    assert await service.mark_revision_conflict(store_id, 400, 1, 40, "stale_revision")
    next_payload = await service.capture(store_id)
    assert next_payload is not None and next_payload["revision"] == 41
    assert next_payload["items"] == first["items"]
    assert not await service.mark_delivered(store_id, 400, 1)
    assert await service.capture(store_id) == next_payload


async def test_delivered_state_is_reconfirmed_after_interval(db_session: AsyncSession) -> None:
    store_id, user_id, _ = await _seed(db_session)
    await _publish(db_session, store_id, user_id, 500)
    service = AvailabilityService(db_session)
    first = await service.capture(store_id)
    assert first is not None
    assert await service.mark_delivered(store_id, 500, 1)
    assert await service.capture(store_id) is None
    row = await db_session.get(OnlineMenuAvailability, store_id)
    assert row is not None
    row.updated_at = datetime.now(UTC) - timedelta(seconds=61)
    await db_session.flush()
    assert await service.capture(store_id) == first
    assert row.delivery_state == "PENDING"
    assert await service.mark_delivered(store_id, 500, 1)
    assert await service.capture(store_id) is None


async def test_signed_availability_request_uses_only_current_state() -> None:
    payload = {
        "menu_version": 3,
        "revision": 8,
        "items": [{"id": 4, "available": True, "remaining": 2}],
        "options": [],
    }
    seen: list[dict[str, object]] = []

    def receive(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/integration/menu/availability"
        signed = canonical_string(
            request.method,
            request.url.raw_path.decode(),
            request.headers["X-LuCamp-Timestamp"],
            request.headers["X-LuCamp-Nonce"],
            request.content,
        )
        assert (
            request.headers["X-LuCamp-Signature"]
            == hmac.new(b"secret", signed.encode(), hashlib.sha256).hexdigest()
        )
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={})

    client = OnlineOrderClient(
        "https://order.test", "secret", store_id=1, transport=httpx.MockTransport(receive)
    )
    assert await client.put_availability(payload) == (200, "", None)
    assert seen == [payload]

    def stale(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "stale_revision", "current_revision": 37})

    stale_client = OnlineOrderClient(
        "https://order.test", "secret", store_id=1, transport=httpx.MockTransport(stale)
    )
    assert await stale_client.put_availability(payload) == (409, "stale_revision", 37)


async def test_late_ack_refreshes_state_after_another_session_advanced_revision() -> None:
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as seed:
        store_id, user_id, item_id = await _seed(seed)
        await _publish(seed, store_id, user_id, 500)
        await seed.commit()
    try:
        async with sessions() as sending, sessions() as changing:
            service = AvailabilityService(sending)
            first = await service.capture(store_id)
            assert first is not None and first["revision"] == 1
            # Retain the ORM instance across commit, as an in-flight sender can do.
            cached = await sending.get(OnlineMenuAvailability, store_id)
            assert cached is not None
            await sending.commit()
            item = await changing.get(MenuItem, item_id)
            assert item is not None
            item.stock_qty = 1
            await changing.flush()
            next_payload = await AvailabilityService(changing).capture(store_id)
            assert next_payload is not None and next_payload["revision"] == 2
            await changing.commit()
            assert cached.revision == 1
            assert not await service.mark_delivered(store_id, 500, 1)
            await sending.commit()
            async with sessions() as checking:
                pending = await checking.get(OnlineMenuAvailability, store_id)
                assert pending is not None
                assert pending.revision == 2 and pending.delivery_state == "PENDING"
    finally:
        async with sessions() as cleanup:
            for model in (OnlineMenuAvailability, OnlineMenuPublication, MenuItem, User):
                await cleanup.execute(delete(model).where(model.store_id == store_id))
            await cleanup.execute(delete(Store).where(Store.id == store_id))
            await cleanup.commit()
