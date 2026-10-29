"""寄售帳篷上官網（docs/97）：POS 維護官網資料、組出推送給官網的清單。

只限在庫的寄售品可以編輯；店員、店長都能改；上架與保留的變更寫稽核；照片最多 8 張；
清單只送白名單欄位，售出的不帶照片、30 天後消失；跨店一律找不到。
"""

import io
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.inventory.models import Brand, SerializedItem
from app.modules.onlineorder.tent_service import TentListingService, feed_sha256
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, SerializedItemStatus, UserRole

BASE = "/api/v1/online-order/tents"


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def session_override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class Seed:
    def __init__(self, store_id: int, clerk: str, manager: str, consignor_id: int) -> None:
        self.store_id = store_id
        self.clerk = clerk
        self.manager = manager
        self.consignor_id = consignor_id


async def seed(session: AsyncSession, name: str = "露坑") -> Seed:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    users = [
        User(store_id=store.id, username=f"{name}-{role.value}", password_hash="h", role=role)
        for role in (UserRole.CLERK, UserRole.MANAGER)
    ]
    consignor = Contact(store_id=store.id, name="寄售人")
    session.add_all([*users, consignor])
    # 測試 session 用 savepoint；commit 才不會被端點出錯時的 rollback 一併撤掉
    await session.commit()
    return Seed(
        store.id,
        encode_access_token(user_id=users[0].id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=users[1].id, role="MANAGER", store_id=store.id),
        consignor.id,
    )


async def item(
    session: AsyncSession,
    s: Seed,
    code: str,
    *,
    name: str = "Amenity Dome M",
    brand: str | None = "Snow Peak",
    price: int = 6800,
    ownership: OwnershipType = OwnershipType.CONSIGNMENT,
    status: SerializedItemStatus = SerializedItemStatus.IN_STOCK,
    sold_date: datetime | None = None,
) -> SerializedItem:
    brand_id = None
    if brand is not None:
        b = await session.scalar(
            select(Brand).where(Brand.store_id == s.store_id, Brand.name == brand)
        )
        if b is None:
            b = Brand(store_id=s.store_id, name=brand)
            session.add(b)
            await session.flush()
        brand_id = b.id
    consigned = ownership is OwnershipType.CONSIGNMENT
    row = SerializedItem(
        store_id=s.store_id,
        item_code=code,
        name=name,
        brand_id=brand_id,
        grade=Grade.A,
        ownership_type=ownership,
        consignor_id=s.consignor_id if consigned else None,
        commission_pct=30 if consigned else None,
        acquisition_cost=None if consigned else Decimal(1000),
        listed_price=Decimal(price),
        status=status,
        sold_date=sold_date,
        note="寄售人說外帳曾淋雨",
    )
    session.add(row)
    await session.commit()
    return row


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "published": True,
        "reserved": False,
        "color": "象牙白",
        "nights": 6,
        "missing": [],
        "defects": ["外帳頂部一處淡色污漬，不影響防水"],
        "extras": ["原廠地墊", "原廠地布"],
        "video_url": "https://youtu.be/cSArnUe7cEY",
    }
    base.update(overrides)
    return base


def _jpeg(color: tuple[int, int, int] = (180, 120, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, format="JPEG")
    return buf.getvalue()


# ── 編輯 ──


async def test_only_in_stock_consignment_items_are_candidates(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    tent = await item(db_session, s, "T-1")
    await item(db_session, s, "OWN-1", ownership=OwnershipType.OWNED)
    await item(
        db_session,
        s,
        "SOLD-1",
        status=SerializedItemStatus.SOLD,
        sold_date=datetime.now(UTC),
    )
    resp = await client.get(BASE, headers=auth(s.clerk))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["item_code"] for r in rows] == ["T-1"]
    row = rows[0]
    assert row["item_id"] == tent.id
    assert (row["name"], row["brand_name"], row["listed_price"]) == (
        "Amenity Dome M",
        "Snow Peak",
        "6800",
    )
    # 還沒填官網資料：預設不上架
    assert (row["published"], row["reserved"], row["photos"]) == (False, False, [])
    # 寄售人、成本、抽成、備註不出現
    for key in ("consignor_id", "commission_pct", "acquisition_cost", "note"):
        assert key not in row


async def test_clerk_saves_listing_and_publish_change_is_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    tent = await item(db_session, s, "T-1")
    resp = await client.put(f"{BASE}/{tent.id}", json=body(), headers=auth(s.clerk))
    assert resp.status_code == 200, resp.text
    row = resp.json()
    assert row["published"] is True
    assert row["defects"] == ["外帳頂部一處淡色污漬，不影響防水"]
    assert row["nights"] == 6
    again = await client.put(
        f"{BASE}/{tent.id}", json=body(reserved=True, color="白"), headers=auth(s.clerk)
    )
    assert again.status_code == 200, again.text
    assert again.json()["reserved"] is True
    audits = list(
        await db_session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "online_tent_listing")
            .order_by(AuditLog.id)
        )
    )
    assert [a.action for a in audits] == [
        "CREATE_ONLINE_TENT_LISTING",
        "UPDATE_ONLINE_TENT_LISTING",
    ]
    assert audits[1].before is not None and audits[1].before["reserved"] is False
    assert audits[1].after is not None and audits[1].after["reserved"] is True


async def test_owned_sold_and_other_store_items_cannot_be_edited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    other = await seed(db_session, "別家")
    owned = await item(db_session, s, "OWN-1", ownership=OwnershipType.OWNED)
    sold = await item(
        db_session, s, "SOLD-1", status=SerializedItemStatus.SOLD, sold_date=datetime.now(UTC)
    )
    foreign = await item(db_session, other, "X-1")
    # 端點出錯會 rollback、讓測試手上的物件過期；先記下 id
    expected = [(owned.id, 409), (sold.id, 409), (foreign.id, 404)]
    for item_id, code in expected:
        resp = await client.put(f"{BASE}/{item_id}", json=body(), headers=auth(s.clerk))
        assert resp.status_code == code, resp.text


async def test_text_lists_are_trimmed_and_limited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    tent = await item(db_session, s, "T-1")
    ok = await client.put(
        f"{BASE}/{tent.id}",
        json=body(missing=["  營釘少 4 支 ", "", "   "], color="  "),
        headers=auth(s.clerk),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["missing"] == ["營釘少 4 支"]
    assert ok.json()["color"] is None
    for bad in (
        body(nights=-1),
        body(extras=["配件"] * 21),
        body(defects=["很" * 101]),
        body(video_url="https://example.com/video"),
        body(color="色" * 31),
    ):
        resp = await client.put(f"{BASE}/{tent.id}", json=bad, headers=auth(s.clerk))
        assert resp.status_code == 422, bad


async def test_any_youtube_link_form_is_accepted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    tent = await item(db_session, s, "T-1")
    for url in (
        "https://www.youtube.com/watch?v=cSArnUe7cEY",
        "https://youtu.be/cSArnUe7cEY?si=abc",
        "https://youtube.com/shorts/cSArnUe7cEY",
    ):
        resp = await client.put(
            f"{BASE}/{tent.id}", json=body(video_url=url), headers=auth(s.clerk)
        )
        assert resp.status_code == 200, url
    cleared = await client.put(
        f"{BASE}/{tent.id}", json=body(video_url="  "), headers=auth(s.clerk)
    )
    assert cleared.json()["video_url"] is None


async def test_photos_upload_reorder_remove_and_cap_at_eight(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    tent = await item(db_session, s, "T-1")
    url = f"{BASE}/{tent.id}/photos"
    # 還沒存過官網資料也能先傳照片
    shas: list[str] = []
    for i in range(8):
        resp = await client.post(
            url,
            files={"file": ("p.jpg", _jpeg((i * 20, 100, 100)), "image/jpeg")},
            headers=auth(s.clerk),
        )
        assert resp.status_code == 200, resp.text
        shas = resp.json()["photos"]
    assert len(shas) == 8
    ninth = await client.post(
        url, files={"file": ("p.jpg", _jpeg((250, 0, 0)), "image/jpeg")}, headers=auth(s.clerk)
    )
    assert ninth.status_code == 409
    reordered = await client.put(
        f"{url}/order", json={"photos": [shas[3], *shas[:3], *shas[4:]]}, headers=auth(s.clerk)
    )
    assert reordered.status_code == 200, reordered.text
    assert reordered.json()["photos"][0] == shas[3]
    bad_order = await client.put(f"{url}/order", json={"photos": shas[:2]}, headers=auth(s.clerk))
    assert bad_order.status_code == 422
    removed = await client.delete(f"{url}/{shas[3]}", headers=auth(s.clerk))
    assert removed.status_code == 200
    assert removed.json()["photos"] == [*shas[:3], *shas[4:]]


async def test_same_photo_twice_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    tent = await item(db_session, s, "T-1")
    url = f"{BASE}/{tent.id}/photos"
    first = await client.post(
        url, files={"file": ("p.jpg", _jpeg(), "image/jpeg")}, headers=auth(s.clerk)
    )
    assert first.status_code == 200
    dup = await client.post(
        url, files={"file": ("p.jpg", _jpeg(), "image/jpeg")}, headers=auth(s.clerk)
    )
    assert dup.status_code == 409


async def test_lookup_by_item_code_tells_pos_whether_reserved(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    tent = await item(db_session, s, "T-1")
    plain = await client.get(f"{BASE}/by-item-code/T-1", headers=auth(s.clerk))
    assert plain.status_code == 200
    assert plain.json() == {"reserved": False}
    await client.put(f"{BASE}/{tent.id}", json=body(reserved=True), headers=auth(s.clerk))
    held = await client.get(f"{BASE}/by-item-code/T-1", headers=auth(s.clerk))
    assert held.json() == {"reserved": True}
    # 沒勾上架的保留不算（官網上看不到，就不必提醒）
    await client.put(
        f"{BASE}/{tent.id}", json=body(reserved=True, published=False), headers=auth(s.clerk)
    )
    hidden = await client.get(f"{BASE}/by-item-code/T-1", headers=auth(s.clerk))
    assert hidden.json() == {"reserved": False}
    missing = await client.get(f"{BASE}/by-item-code/NOPE", headers=auth(s.clerk))
    assert missing.json() == {"reserved": False}


# ── 推給官網的清單 ──


async def _listed(
    client: httpx.AsyncClient, s: Seed, row: SerializedItem, **overrides: object
) -> None:
    resp = await client.put(f"{BASE}/{row.id}", json=body(**overrides), headers=auth(s.clerk))
    assert resp.status_code == 200, resp.text


async def test_feed_status_mapping_and_sold_window(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    now = datetime(2026, 10, 30, 6, 20, tzinfo=UTC)
    available = await item(db_session, s, "T-1")
    held = await item(db_session, s, "T-2", name="Pilz 15", brand="OGAWA")
    recent = await item(db_session, s, "T-3", name="Elfield")
    old = await item(db_session, s, "T-4", name="Old")
    hidden = await item(db_session, s, "T-5", name="Hidden")
    returned = await item(db_session, s, "T-6", name="Returned")
    for row in (available, recent, old, returned):
        await _listed(client, s, row)
    await _listed(client, s, held, reserved=True)
    await _listed(client, s, hidden, published=False)
    recent.status, recent.sold_date = SerializedItemStatus.SOLD, now - timedelta(days=29)
    old.status, old.sold_date = SerializedItemStatus.SOLD, now - timedelta(days=31)
    returned.status = SerializedItemStatus.RETURNED_TO_CONSIGNOR
    await db_session.flush()

    feed = await TentListingService(db_session).build_feed(s.store_id, now=now)
    by_id = {t["id"]: t for t in feed["tents"]}
    assert set(by_id) == {"T-1", "T-2", "T-3"}
    assert (by_id["T-1"]["status"], by_id["T-2"]["status"], by_id["T-3"]["status"]) == (
        "available",
        "reserved",
        "sold",
    )
    assert feed["soldDays"] == 30
    # 售出時間以台北時間呈現
    assert by_id["T-3"]["updatedAt"] == (now - timedelta(days=29)).astimezone(
        ZoneInfo("Asia/Taipei")
    ).isoformat(timespec="seconds")


async def test_feed_sends_only_whitelisted_fields_and_no_photos_when_sold(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    tent = await item(db_session, s, "T-1", brand=None)
    await _listed(client, s, tent)
    up = await client.post(
        f"{BASE}/{tent.id}/photos",
        files={"file": ("p.jpg", _jpeg(), "image/jpeg")},
        headers=auth(s.clerk),
    )
    sha = up.json()["photos"][0]
    now = datetime.now(UTC)
    feed = await TentListingService(db_session).build_feed(s.store_id, now=now)
    [entry] = feed["tents"]
    assert set(entry) == {
        "id", "brand", "name", "color", "nights", "missing", "defects", "extras",
        "photos", "video", "price", "status", "listedAt", "updatedAt",
    }  # fmt: skip
    assert entry["brand"] is None
    assert entry["price"] == 6800
    assert entry["photos"] == [sha]
    assert entry["video"] == "https://youtu.be/cSArnUe7cEY"
    assert "寄售人" not in str(feed) and "淋雨" not in str(feed)

    tent.status, tent.sold_date = SerializedItemStatus.SOLD, now
    await db_session.flush()
    feed = await TentListingService(db_session).build_feed(s.store_id, now=now)
    assert feed["tents"][0]["photos"] == []


async def test_feed_hash_changes_only_when_content_changes(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    tent = await item(db_session, s, "T-1")
    await _listed(client, s, tent)
    now = datetime.now(UTC)
    svc = TentListingService(db_session)
    first = feed_sha256(await svc.build_feed(s.store_id, now=now))
    later = feed_sha256(await svc.build_feed(s.store_id, now=now + timedelta(minutes=5)))
    assert first == later
    tent.listed_price = Decimal(6500)
    await db_session.flush()
    assert feed_sha256(await svc.build_feed(s.store_id, now=now)) != first


async def test_sold_items_lose_their_reserved_mark(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    tent = await item(db_session, s, "T-1")
    await _listed(client, s, tent, reserved=True)
    tent.status, tent.sold_date = SerializedItemStatus.SOLD, datetime.now(UTC)
    await db_session.flush()
    svc = TentListingService(db_session)
    assert await svc.clear_reserved_for_sold(s.store_id) == 1
    # 退貨回庫：不會又變成保留中
    tent.status, tent.sold_date = SerializedItemStatus.IN_STOCK, None
    await db_session.flush()
    feed = await svc.build_feed(s.store_id, now=datetime.now(UTC))
    assert feed["tents"][0]["status"] == "available"
