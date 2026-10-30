"""寄售帳篷預售（docs/102）：帳篷還在賣家家裡就先上官網；到店建寄售單時帶入，官網變現貨。

預售不收身分證、不建序號品；賣家姓名電話只在 POS；寄售單帶入失敗整張不建立。
"""

import hashlib
import io
import itertools
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.schemas import AcquisitionCreate
from app.modules.acquisition.service import AcquisitionService
from app.modules.inventory.models import Brand, SerializedItem
from app.modules.onlineorder.tent_service import TentListingService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

BASE = "/api/v1/online-order/tents"
PRESALES = f"{BASE}/presales"


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
    def __init__(self, store_id: int, clerk: str, brand_id: int) -> None:
        self.store_id = store_id
        self.clerk = clerk
        self.brand_id = brand_id


async def seed(session: AsyncSession, name: str = "露坑") -> Seed:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id, username=f"{name}-clerk", password_hash="h", role=UserRole.CLERK
    )
    brand = Brand(store_id=store.id, name="KZM")
    session.add_all([clerk, brand])
    # 測試 session 用 savepoint；commit 才不會被端點出錯時的 rollback 一併撤掉
    await session.commit()
    return Seed(
        store.id,
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        brand.id,
    )


_keys = itertools.count()
_phones = itertools.count(1)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"presale-{next(_keys)}"}


def presale_body(s: Seed, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "brand_id": s.brand_id,
        "name": "NEW X5",
        "price": "14999",
        "seller_name": "王小明",
        "seller_phone": "0912345678",
        "note": "表單 10/31 填的，週六可以帶來",
        "published": True,
        "reserved": False,
        "color": "灰",
        "nights": 4,
        "missing": [],
        "defects": [],
        "extras": ["贈客廳專屬地墊"],
        "video_url": None,
    }
    base.update(overrides)
    return base


def _jpeg(color: tuple[int, int, int] = (180, 120, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, format="JPEG")
    return buf.getvalue()


async def create_presale(client: httpx.AsyncClient, s: Seed, **overrides: object) -> dict[str, Any]:
    resp = await client.post(PRESALES, json=presale_body(s, **overrides), headers=auth(s.clerk))
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()
    return data


async def add_photo(
    client: httpx.AsyncClient, s: Seed, listing_id: int, color: tuple[int, int, int]
) -> dict[str, Any]:
    resp = await client.post(
        f"{PRESALES}/{listing_id}/photos",
        files={"file": ("a.jpg", _jpeg(color), "image/jpeg")},
        headers=auth(s.clerk),
    )
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()
    return data


async def feed(session: AsyncSession, s: Seed) -> dict[str, Any]:
    return await TentListingService(session).build_feed(s.store_id, now=datetime.now(UTC))


async def make_seller(client: httpx.AsyncClient, s: Seed) -> int:
    resp = await client.post(
        "/api/v1/contacts",
        json={
            "name": "王小明",
            "phone": f"09{next(_phones):08d}",
            "roles": ["SELLER"],
            "national_id": "A123456789",
        },
        headers=auth(s.clerk),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def consign(
    client: httpx.AsyncClient,
    s: Seed,
    contact_id: int,
    presale_id: int | None,
    *,
    acq_type: str = "CONSIGNMENT",
    price: str = "13999",
) -> httpx.Response:
    item: dict[str, object] = {
        "name": "NEW X5 灰",
        "grade": "A",
        "listed_price": price,
        "brand_id": s.brand_id,
    }
    if acq_type == "BUYOUT":
        item["acquisition_cost"] = "5000"
    if presale_id is not None:
        item["presale_listing_id"] = presale_id
    return await client.post(
        "/api/v1/acquisitions",
        json={"type": acq_type, "contact_id": contact_id, "items": [item]},
        headers=auth(s.clerk),
    )


# ── 建預售 ──


async def test_clerk_creates_presale_without_national_id(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    row = await create_presale(client, s)
    assert row["presale_code"].startswith(f"P{s.store_id}-")
    assert len(row["presale_code"]) == len(f"P{s.store_id}-") + 10
    assert (row["name"], row["brand_name"], row["price"]) == ("NEW X5", "KZM", "14999")
    assert (row["seller_name"], row["seller_phone"]) == ("王小明", "0912345678")
    assert (row["published"], row["nights"], row["extras"]) == (True, 4, ["贈客廳專屬地墊"])
    assert row["photos"] == []

    listed = await client.get(PRESALES, headers=auth(s.clerk))
    assert listed.status_code == 200, listed.text
    assert [r["listing_id"] for r in listed.json()] == [row["listing_id"]]

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.entity_type == "online_tent_listing",
            AuditLog.action == "CREATE_ONLINE_TENT_PRESALE",
        )
    )
    assert audit is not None
    assert audit.entity_id == str(row["listing_id"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "  "},
        {"price": "0"},
        {"price": "100.5"},
        {"seller_name": ""},
        {"seller_phone": " "},
        {"brand_id": None},
    ],
)
async def test_presale_requires_name_price_brand_and_seller(
    client: httpx.AsyncClient, db_session: AsyncSession, overrides: dict[str, object]
) -> None:
    s = await seed(db_session)
    resp = await client.post(PRESALES, json=presale_body(s, **overrides), headers=auth(s.clerk))
    assert resp.status_code == 422, resp.text


async def test_presale_brand_must_belong_to_store(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    other = await seed(db_session, "別店")
    resp = await client.post(
        PRESALES, json=presale_body(s, brand_id=other.brand_id), headers=auth(s.clerk)
    )
    assert resp.status_code == 404, resp.text


async def test_update_and_cancel_presale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    row = await create_presale(client, s)
    lid = row["listing_id"]
    resp = await client.put(
        f"{PRESALES}/{lid}",
        json=presale_body(s, price="12999", reserved=True),
        headers=auth(s.clerk),
    )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["price"], resp.json()["reserved"]) == ("12999", True)

    cancelled = await client.post(f"{PRESALES}/{lid}/cancel", headers=auth(s.clerk))
    assert cancelled.status_code == 204, cancelled.text
    assert (await client.get(PRESALES, headers=auth(s.clerk))).json() == []
    assert (await feed(db_session, s))["tents"] == []
    # 取消後不能再改
    again = await client.put(f"{PRESALES}/{lid}", json=presale_body(s), headers=auth(s.clerk))
    assert again.status_code == 409, again.text
    actions = list(
        await db_session.scalars(
            select(AuditLog.action)
            .where(AuditLog.entity_type == "online_tent_listing")
            .order_by(AuditLog.id)
        )
    )
    assert actions == [
        "CREATE_ONLINE_TENT_PRESALE",
        "UPDATE_ONLINE_TENT_PRESALE",
        "CANCEL_ONLINE_TENT_PRESALE",
    ]


async def test_presale_of_other_store_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    other = await seed(db_session, "別店")
    row = await create_presale(client, s)
    resp = await client.put(
        f"{PRESALES}/{row['listing_id']}", json=presale_body(other), headers=auth(other.clerk)
    )
    assert resp.status_code == 404, resp.text
    cancel = await client.post(f"{PRESALES}/{row['listing_id']}/cancel", headers=auth(other.clerk))
    assert cancel.status_code == 404, cancel.text


async def test_presale_photos_add_reorder_remove(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    lid = (await create_presale(client, s))["listing_id"]
    first = (await add_photo(client, s, lid, (10, 20, 30)))["photos"][0]
    second = (await add_photo(client, s, lid, (200, 100, 50)))["photos"][1]
    resp = await client.put(
        f"{PRESALES}/{lid}/photos/order",
        json={"photos": [second, first]},
        headers=auth(s.clerk),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["photos"] == [second, first]
    removed = await client.delete(f"{PRESALES}/{lid}/photos/{second}", headers=auth(s.clerk))
    assert removed.status_code == 200, removed.text
    assert removed.json()["photos"] == [first]


# ── 官網清單 ──


async def test_published_presale_is_in_feed_without_seller_data(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    row = await create_presale(client, s)
    sha = (await add_photo(client, s, row["listing_id"], (10, 20, 30)))["photos"][0]
    await create_presale(client, s, name="沒上架的", published=False)
    data = await feed(db_session, s)
    assert len(data["tents"]) == 1
    tent = data["tents"][0]
    assert tent["id"] == row["presale_code"]
    assert (tent["brand"], tent["name"], tent["price"]) == ("KZM", "NEW X5", 14999)
    assert (tent["status"], tent["presale"], tent["formerIds"]) == ("available", True, [])
    assert tent["photos"] == [sha]
    text = json.dumps(data, ensure_ascii=False)
    for secret in ("王小明", "0912345678", "表單 10/31"):
        assert secret not in text


async def test_reserved_presale_shows_reserved_and_is_kept_by_sold_cleanup(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    await create_presale(client, s, reserved=True)
    assert (await feed(db_session, s))["tents"][0]["status"] == "reserved"
    # 「售出就清保留」只看序號品；預售還沒有序號品，不能被誤清
    assert await TentListingService(db_session).clear_reserved_for_sold(s.store_id) == 0
    assert (await feed(db_session, s))["tents"][0]["status"] == "reserved"


# ── 到店轉寄售 ──


async def test_consignment_with_presale_takes_over_listing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    row = await create_presale(client, s, reserved=True)
    sha = (await add_photo(client, s, row["listing_id"], (10, 20, 30)))["photos"][0]
    contact_id = await make_seller(client, s)

    resp = await consign(client, s, contact_id, row["listing_id"])
    assert resp.status_code == 201, resp.text
    code = resp.json()["item_codes"][0]

    # 預售清單不再列它；在庫寄售品清單接手官網資料與照片
    assert (await client.get(PRESALES, headers=auth(s.clerk))).json() == []
    tents = (await client.get(BASE, headers=auth(s.clerk))).json()
    taken = next(t for t in tents if t["item_code"] == code)
    assert (taken["published"], taken["reserved"], taken["nights"]) == (True, True, 4)
    assert taken["photos"] == [sha]

    tent = (await feed(db_session, s))["tents"][0]
    assert tent["id"] == code
    assert (tent["presale"], tent["formerIds"]) == (False, [row["presale_code"]])
    # 品名與售價以寄售單為準
    assert (tent["name"], tent["price"]) == ("NEW X5 灰", 13999)


async def test_presale_cannot_be_taken_twice(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    row = await create_presale(client, s)
    contact_id = await make_seller(client, s)
    assert (await consign(client, s, contact_id, row["listing_id"])).status_code == 201
    before = await db_session.scalar(select(func.count()).select_from(Acquisition))
    items_before = await db_session.scalar(select(func.count()).select_from(SerializedItem))

    again = await consign(client, s, contact_id, row["listing_id"])
    assert again.status_code == 409, again.text
    assert "預售" in again.json()["detail"]
    # 整張寄售單不建立
    assert await db_session.scalar(select(func.count()).select_from(Acquisition)) == before
    assert await db_session.scalar(select(func.count()).select_from(SerializedItem)) == items_before


async def test_cancelled_or_other_store_presale_rejects_consignment(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    other = await seed(db_session, "別店")
    cancelled = await create_presale(client, s)
    await client.post(f"{PRESALES}/{cancelled['listing_id']}/cancel", headers=auth(s.clerk))
    foreign = await create_presale(client, other)
    contact_id = await make_seller(client, s)
    for lid in (cancelled["listing_id"], foreign["listing_id"]):
        resp = await consign(client, s, contact_id, lid)
        assert resp.status_code == 409, resp.text
    assert await db_session.scalar(select(func.count()).select_from(Acquisition)) == 0


async def test_buyout_cannot_take_presale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await seed(db_session)
    row = await create_presale(client, s)
    contact_id = await make_seller(client, s)
    resp = await consign(client, s, contact_id, row["listing_id"], acq_type="BUYOUT")
    assert resp.status_code == 422, resp.text


async def test_consignment_without_presale_unchanged_fingerprint() -> None:
    """沒帶預售時冪等指紋退化成加欄位前的形狀（部署前送出的重送不能變成 409，見 _fingerprint）。"""
    data = AcquisitionCreate.model_validate(
        {
            "type": "CONSIGNMENT",
            "contact_id": 1,
            "items": [{"name": "帳篷", "grade": "A", "listed_price": "3000"}],
        }
    )
    payload = data.model_dump(mode="json")
    for item in payload["items"]:
        for key in ("resale_discount_pct", "note", "presale_listing_id"):
            assert item.pop(key, None) is None
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert AcquisitionService._fingerprint(data) == expected
