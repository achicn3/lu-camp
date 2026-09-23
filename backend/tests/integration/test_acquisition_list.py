"""收購紀錄清單整合測試（2026-09-23 裁示）。

店員也能看清單，作廢鈕限管理者；清單事先算好「這張能不能作廢、為什麼不行」，
店長不必按下去才被拒絕。判斷口徑與作廢端點一致（寄售、已作廢、含已售出、
購物金已用掉、付現但沒開帳）；作廢端點仍是最終權威。
"""

import itertools
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import SerializedItem
from app.modules.inventory.service import InventoryService
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import StoreCreditSourceType, UserRole

PATH = "/api/v1/acquisitions"


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


_idem = itertools.count()


class Shop:
    def __init__(self, store_id: int, clerk_id: int, clerk: str, manager: str) -> None:
        self.store_id = store_id
        self.clerk_id = clerk_id
        self.clerk = clerk
        self.manager = manager


async def _seed(db: AsyncSession, *, name: str = "門市") -> Shop:
    store = Store(name=name)
    db.add(store)
    await db.flush()
    clerk = User(
        store_id=store.id, username=f"阿明{store.id}", password_hash="h", role=UserRole.CLERK
    )
    mgr = User(
        store_id=store.id, username=f"店長{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    db.add_all([clerk, mgr])
    await db.flush()
    await CashDrawerService(db).open_session(store.id, clerk.id, Decimal("50000"))
    return Shop(
        store.id,
        clerk.id,
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
    )


async def _seller(db: AsyncSession, store_id: int, name: str) -> int:
    contact = Contact(
        store_id=store_id, name=name, roles=["SELLER", "MEMBER"], national_id_enc="enc"
    )
    db.add(contact)
    await db.flush()
    return contact.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"list-{next(_idem)}"}


async def _buyout(
    client: httpx.AsyncClient,
    token: str,
    contact_id: int,
    names: tuple[str, ...] = ("帳篷",),
    *,
    payout_method: str = "CASH",
) -> int:
    resp = await client.post(
        PATH,
        json={
            "type": "BUYOUT",
            "contact_id": contact_id,
            "payout_method": payout_method,
            "items": [
                {"name": n, "grade": "A", "acquisition_cost": "1000", "listed_price": "1800"}
                for n in names
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["acquisition_id"])


async def _consignment(client: httpx.AsyncClient, token: str, contact_id: int) -> int:
    resp = await client.post(
        PATH,
        json={
            "type": "CONSIGNMENT",
            "contact_id": contact_id,
            "items": [{"name": "寄賣椅", "grade": "A", "listed_price": "900"}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["acquisition_id"])


def _row(body: dict[str, object], acq_id: int) -> dict[str, object]:
    items = body["items"]
    assert isinstance(items, list)
    return next(r for r in items if r["id"] == acq_id)


async def test_lists_newest_first_with_seller_items_and_payout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _seed(db_session)
    seller = await _seller(db_session, shop.store_id, "林賣家")
    first = await _buyout(client, shop.clerk, seller, ("焚火台",))
    second = await _buyout(client, shop.clerk, seller, ("營燈", "睡袋", "爐頭", "鍋具"))

    resp = await client.get(PATH, headers=_auth(shop.clerk))  # 店員也能看
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert [r["id"] for r in body["items"]] == [second, first]
    row = _row(body, second)
    assert row["seller_name"] == "林賣家"
    assert row["clerk_name"] == f"阿明{shop.store_id}"
    assert row["type"] == "BUYOUT"
    assert row["item_count"] == 4
    assert row["item_names"] == ["營燈", "睡袋", "爐頭"]  # 最多列 3 個，其餘看件數
    assert row["payout_method"] == "CASH"
    assert row["payout_cash_amount"] == "4000"
    assert row["voided_at"] is None
    assert row["void_block"] is None


async def test_paging(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    shop = await _seed(db_session)
    seller = await _seller(db_session, shop.store_id, "林賣家")
    ids = [await _buyout(client, shop.clerk, seller) for _ in range(3)]

    page = await client.get(PATH, params={"limit": 2, "offset": 2}, headers=_auth(shop.clerk))
    body = page.json()
    assert body["total"] == 3
    assert [r["id"] for r in body["items"]] == [ids[0]]


async def test_void_block_reasons_are_precomputed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """不能作廢的單事先標出原因，口徑與作廢端點一致。"""
    shop = await _seed(db_session)
    seller = await _seller(db_session, shop.store_id, "林賣家")
    credit_seller = await _seller(db_session, shop.store_id, "王會員")

    ok = await _buyout(client, shop.clerk, seller)
    consigned = await _consignment(client, shop.clerk, seller)
    voided = await _buyout(client, shop.clerk, seller)
    void = await client.post(
        f"{PATH}/{voided}/void", json={"reason": "登錄錯誤"}, headers=_auth(shop.manager)
    )
    assert void.status_code == 200, void.text

    sold = await _buyout(client, shop.clerk, seller)
    item = await db_session.scalar(
        select(SerializedItem).where(SerializedItem.acquisition_id == sold)
    )
    assert item is not None
    await InventoryService(db_session).sell_serialized_item(item.id)

    spent = await _buyout(client, shop.clerk, credit_seller, payout_method="STORE_CREDIT")
    await StoreCreditService(db_session).debit(
        shop.store_id,
        credit_seller,
        amount=Decimal("100"),
        source_type=StoreCreditSourceType.SALE,
        source_id=999,
        created_by=shop.clerk_id,
    )

    body = (await client.get(PATH, headers=_auth(shop.manager))).json()
    assert _row(body, ok)["void_block"] is None
    assert _row(body, consigned)["void_block"] == "CONSIGNMENT"
    assert _row(body, voided)["void_block"] == "ALREADY_VOIDED"
    assert _row(body, voided)["voided_at"] is not None
    assert _row(body, sold)["void_block"] == "HAS_SOLD_ITEMS"
    assert _row(body, spent)["void_block"] == "CREDIT_SPENT"


async def test_cash_payout_needs_open_cash_session(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """付現的單在沒開帳時標「需先開帳」；付購物金的不受影響。"""
    shop = await _seed(db_session)
    seller = await _seller(db_session, shop.store_id, "林賣家")
    cash = await _buyout(client, shop.clerk, seller)
    credit = await _buyout(client, shop.clerk, seller, payout_method="STORE_CREDIT")
    drawer = CashDrawerService(db_session)
    session = await drawer.get_current_session(shop.store_id)
    assert session is not None
    await drawer.close_session(session, Decimal("49000"), closed_by=shop.clerk_id)

    body = (await client.get(PATH, headers=_auth(shop.manager))).json()
    assert _row(body, cash)["void_block"] == "NO_OPEN_CASH_SESSION"
    assert _row(body, credit)["void_block"] is None


async def test_filters_by_type_voided_seller_and_date(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _seed(db_session)
    lin = await _seller(db_session, shop.store_id, "林賣家")
    wang = await _seller(db_session, shop.store_id, "王小姐")
    buyout = await _buyout(client, shop.clerk, lin)
    consigned = await _consignment(client, shop.clerk, wang)
    voided = await _buyout(client, shop.clerk, wang)
    await client.post(f"{PATH}/{voided}/void", json={"reason": "x"}, headers=_auth(shop.manager))
    old = await _buyout(client, shop.clerk, lin)
    acq = await db_session.get(Acquisition, old)
    assert acq is not None
    acq.created_at = datetime.now(UTC) - timedelta(days=40)
    await db_session.flush()

    async def ids(**params: str) -> list[int]:
        resp = await client.get(PATH, params=params, headers=_auth(shop.clerk))
        assert resp.status_code == 200, resp.text
        return [int(r["id"]) for r in resp.json()["items"]]

    assert await ids(type="CONSIGNMENT") == [consigned]
    assert await ids(voided="true") == [voided]
    assert set(await ids(voided="false")) == {buyout, consigned, old}
    assert set(await ids(q="王")) == {consigned, voided}
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    assert old not in await ids(date_from=since)
    until = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    assert await ids(date_to=until) == [old]


async def test_store_scoped(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mine = await _seed(db_session)
    other = await _seed(db_session, name="他店")
    seller = await _seller(db_session, other.store_id, "他店賣家")
    await _buyout(client, other.clerk, seller)

    body = (await client.get(PATH, headers=_auth(mine.clerk))).json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_limit_is_bounded(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    shop = await _seed(db_session)
    for limit in (0, 101):
        resp = await client.get(PATH, params={"limit": limit}, headers=_auth(shop.clerk))
        assert resp.status_code == 422
