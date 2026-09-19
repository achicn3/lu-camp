"""商品全新售價（原價）：收購時記下、事後可改、讀得回來（2026-09-19 裁示）。

二手店開價要有個對照：客人問「這頂帳篷值不值」，店員能指著標價說「全新要 8,000」。
原價**純記錄**——不參與任何計算、不影響毛利與報表，只是跟著商品走的一個數字。

守的三件事：
1. 收購（序號品／散裝批）可以填原價，存得進去也讀得回來。
2. 事後在編輯裡可以補填或改掉（打錯字要能修）。
3. 不填就是不填——沒有原價的商品照常運作，不可因此擋下收購。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import Category
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole
from tests.integration.customer_display_helpers import CustomerDisplayAwareClient


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with CustomerDisplayAwareClient(
        transport=transport, base_url="http://test", db_session=db_session
    ) as c:
        yield c
    app.dependency_overrides.clear()


class Seeded:
    def __init__(self, *, manager_token: str, store_id: int, seller_id: int, category_id: int):
        self.manager_token = manager_token
        self.store_id = store_id
        self.seller_id = seller_id
        self.category_id = category_id


async def _seed(session: AsyncSession) -> Seeded:
    store = Store(name="原價測試店")
    session.add(store)
    await session.flush()
    manager = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add(manager)
    await session.flush()
    seller = Contact(
        store_id=store.id,
        name="王小明",
        phone="0912345678",
        national_id_enc=get_pii_cipher().encrypt("A123456789"),
        national_id_blind_index=national_id_blind_index("A123456789"),
        roles=["SELLER"],
    )
    category = Category(store_id=store.id, name="帳篷", target_margin_pct=45)
    session.add_all([seller, category])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, manager.id, Decimal(10000))
    return Seeded(
        manager_token=encode_access_token(
            user_id=manager.id, role="MANAGER", store_id=store.id
        ),
        store_id=store.id,
        seller_id=seller.id,
        category_id=category.id,
    )


_next_key = 0


def _auth(token: str) -> dict[str, str]:
    """收購是冪等端點：每次呼叫要帶一組不重複的鍵，否則第二筆會被當成重送。"""
    global _next_key
    _next_key += 1
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"retail-{_next_key:04d}"}


async def test_buyout_records_retail_price_on_the_item(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收購序號品時填的原價，要能從庫存讀回來。"""
    seeded = await _seed(db_session)
    created = await client.post(
        "/api/v1/acquisitions",
        headers=_auth(seeded.manager_token),
        json={
            "type": "BUYOUT",
            "contact_id": seeded.seller_id,
            "payout_method": "CASH",
            "items": [
                {
                    "name": "北歐風帳篷",
                    "grade": "A",
                    "category_id": seeded.category_id,
                    "listed_price": "3500",
                    "acquisition_cost": "1500",
                    "retail_price": "8000",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text

    listing = await client.get(
        "/api/v1/serialized-items",
        headers=_auth(seeded.manager_token),
        params={"limit": 50},
    )
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["retail_price"] == "8000"
    # 原價純記錄：不可污染售價或成本。
    assert rows[0]["listed_price"] == "3500"


async def test_retail_price_is_optional(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """不填原價照樣收得進來——多數小東西沒人記得全新價。"""
    seeded = await _seed(db_session)
    created = await client.post(
        "/api/v1/acquisitions",
        headers=_auth(seeded.manager_token),
        json={
            "type": "BUYOUT",
            "contact_id": seeded.seller_id,
            "payout_method": "CASH",
            "items": [
                {
                    "name": "營釘一包",
                    "grade": "B",
                    "category_id": seeded.category_id,
                    "listed_price": "120",
                    "acquisition_cost": "40",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    rows = (
        await client.get(
            "/api/v1/serialized-items",
            headers=_auth(seeded.manager_token),
            params={"limit": 50},
        )
    ).json()
    assert rows[0]["retail_price"] is None


async def test_bulk_lot_records_retail_price(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """散裝批也要有原價（整批同款時照樣用得上）。"""
    seeded = await _seed(db_session)
    created = await client.post(
        "/api/v1/acquisitions",
        headers=_auth(seeded.manager_token),
        json={
            "type": "BULK_LOT",
            "contact_id": seeded.seller_id,
            "payout_method": "CASH",
            "lot": {
                "name": "露營小物堆",
                "acquisition_cost": "600",
                "acquisition_basis": "BAG",
                "total_qty": 20,
                "unit_price": "60",
                "category_id": seeded.category_id,
                "retail_price": "150",
            },
        },
    )
    assert created.status_code == 201, created.text
    rows = (
        await client.get(
            "/api/v1/bulk-lots",
            headers=_auth(seeded.manager_token),
            params={"limit": 50},
        )
    ).json()
    assert rows[0]["retail_price"] == "150"


async def test_edit_can_set_and_clear_retail_price(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """事後補填、改掉、清空都要做得到——收購當下常常來不及查全新價。"""
    seeded = await _seed(db_session)
    await client.post(
        "/api/v1/acquisitions",
        headers=_auth(seeded.manager_token),
        json={
            "type": "BUYOUT",
            "contact_id": seeded.seller_id,
            "payout_method": "CASH",
            "items": [
                {
                    "name": "北歐風帳篷",
                    "grade": "A",
                    "category_id": seeded.category_id,
                    "listed_price": "3500",
                    "acquisition_cost": "1500",
                }
            ],
        },
    )
    item_id = (
        await client.get(
            "/api/v1/serialized-items",
            headers=_auth(seeded.manager_token),
            params={"limit": 50},
        )
    ).json()[0]["id"]

    filled = await client.patch(
        f"/api/v1/serialized-items/{item_id}",
        headers=_auth(seeded.manager_token),
        json={"retail_price": "8000"},
    )
    assert filled.status_code == 200, filled.text
    assert filled.json()["retail_price"] == "8000"
    # 只送原價時品名不可被清掉。
    assert filled.json()["name"] == "北歐風帳篷"

    renamed = await client.patch(
        f"/api/v1/serialized-items/{item_id}",
        headers=_auth(seeded.manager_token),
        json={"name": "北歐風帳篷（二手）"},
    )
    assert renamed.status_code == 200, renamed.text
    # 沒送原價就不動它——否則改個名字會把原價洗掉。
    assert renamed.json()["retail_price"] == "8000"
    assert renamed.json()["name"] == "北歐風帳篷（二手）"

    cleared = await client.patch(
        f"/api/v1/serialized-items/{item_id}",
        headers=_auth(seeded.manager_token),
        json={"retail_price": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["retail_price"] is None


async def test_retail_price_rejects_negative(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """負的原價沒有意義，要在邊界擋下而不是存進去。"""
    seeded = await _seed(db_session)
    rejected = await client.post(
        "/api/v1/acquisitions",
        headers=_auth(seeded.manager_token),
        json={
            "type": "BUYOUT",
            "contact_id": seeded.seller_id,
            "payout_method": "CASH",
            "items": [
                {
                    "name": "北歐風帳篷",
                    "grade": "A",
                    "category_id": seeded.category_id,
                    "listed_price": "3500",
                    "acquisition_cost": "1500",
                    "retail_price": "-1",
                }
            ],
        },
    )
    assert rejected.status_code == 422, rejected.text
