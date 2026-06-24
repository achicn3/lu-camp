"""會員點數累積整合測試（docs/16 §0）。

規則：floor(含稅總額 total ÷ 100) 點，每筆交易一次、結帳交易內累積（有
buyer_contact_id 才計）；收購不給點；void 同交易沖回；本階段僅累積、不兌換。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import CatalogProduct
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole


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


async def _seed(session: AsyncSession) -> tuple[str, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id


async def _seed_member(session: AsyncSession, store_id: int, *, points: int = 0) -> Contact:
    member = Contact(store_id=store_id, name="王小明", member_points=points)
    session.add(member)
    await session.flush()
    return member


async def _seed_catalog(session: AsyncSession, store_id: int, *, price: str) -> int:
    product = CatalogProduct(
        store_id=store_id, sku="SKU1", name="飲料", unit_price=Decimal(price), quantity_on_hand=99
    )
    session.add(product)
    await session.flush()
    return product.id


def _auth(token: str, idem: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": idem}


async def _checkout(
    client: httpx.AsyncClient,
    token: str,
    catalog_id: int,
    *,
    qty: int = 1,
    buyer: int | None = None,
    idem: str = "k1",
) -> httpx.Response:
    payload: dict[str, object] = {
        "lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": qty}]
    }
    if buyer is not None:
        payload["buyer_contact_id"] = buyer
    return await client.post("/api/v1/sales", json=payload, headers=_auth(token, idem))


async def test_member_sale_accrues_floor_points(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """total 1090 → floor(1090/100) = 10 點（floor 非四捨五入）。"""
    token, store_id = await _seed(db_session)
    member = await _seed_member(db_session, store_id, points=5)
    cat = await _seed_catalog(db_session, store_id, price="1090")
    resp = await _checkout(client, token, cat, buyer=member.id)
    assert resp.status_code == 201
    await db_session.refresh(member)
    assert member.member_points == 15  # 5 + 10


async def test_small_total_accrues_zero_points(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id = await _seed(db_session)
    member = await _seed_member(db_session, store_id)
    cat = await _seed_catalog(db_session, store_id, price="99")
    resp = await _checkout(client, token, cat, buyer=member.id)
    assert resp.status_code == 201
    await db_session.refresh(member)
    assert member.member_points == 0


async def test_sale_without_buyer_accrues_nothing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id = await _seed(db_session)
    member = await _seed_member(db_session, store_id, points=7)
    cat = await _seed_catalog(db_session, store_id, price="500")
    resp = await _checkout(client, token, cat)  # 無 buyer
    assert resp.status_code == 201
    await db_session.refresh(member)
    assert member.member_points == 7  # 不變


async def test_idempotent_replay_accrues_once(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同 Idempotency-Key 重送回原單，點數只累積一次。"""
    token, store_id = await _seed(db_session)
    member = await _seed_member(db_session, store_id)
    cat = await _seed_catalog(db_session, store_id, price="300")
    first = await _checkout(client, token, cat, buyer=member.id, idem="same-key")
    replay = await _checkout(client, token, cat, buyer=member.id, idem="same-key")
    assert first.status_code == 201
    assert replay.status_code in (200, 201)
    assert replay.json()["id"] == first.json()["id"]
    await db_session.refresh(member)
    assert member.member_points == 3  # 只加一次


async def test_void_claws_back_points(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """作廢沖回該筆累積的點數（docs/16 §0；點數僅累積故不會為負）。"""
    token, store_id = await _seed(db_session)
    member = await _seed_member(db_session, store_id, points=2)
    cat = await _seed_catalog(db_session, store_id, price="800")
    sale = await _checkout(client, token, cat, buyer=member.id)
    assert sale.status_code == 201
    await db_session.refresh(member)
    assert member.member_points == 10  # 2 + 8
    from tests.integration.test_sales_api import _seed_manager  # MANAGER 才能 void

    mgr_token = await _seed_manager(db_session, store_id)
    void = await client.post(
        f"/api/v1/sales/{sale.json()['id']}/void",
        headers={"Authorization": f"Bearer {mgr_token}"},
    )
    assert void.status_code == 200
    await db_session.refresh(member)
    assert member.member_points == 2  # 沖回 8


async def test_void_legacy_sale_claws_back_nothing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """歷史單（點數功能上線前、awarded_points=0）作廢不得倒扣（Codex P1）：
    沖回以「當時實際累積」為準、不重算。"""
    token, store_id = await _seed(db_session)
    member = await _seed_member(db_session, store_id, points=2)
    cat = await _seed_catalog(db_session, store_id, price="800")
    sale_resp = await _checkout(client, token, cat, buyer=member.id)
    assert sale_resp.status_code == 201
    sale_id = sale_resp.json()["id"]
    # 模擬歷史單：當時沒有點數功能 → awarded_points=0，並還原會員點數
    from app.modules.sales.models import Sale

    sale_row = await db_session.get(Sale, sale_id)
    assert sale_row is not None
    sale_row.awarded_points = 0
    member.member_points = 2
    await db_session.flush()
    from tests.integration.test_sales_api import _seed_manager

    mgr_token = await _seed_manager(db_session, store_id)
    void = await client.post(
        f"/api/v1/sales/{sale_id}/void", headers={"Authorization": f"Bearer {mgr_token}"}
    )
    assert void.status_code == 200  # 不被點數不足擋下
    await db_session.refresh(member)
    assert member.member_points == 2  # 沒給過就不倒扣


async def test_void_with_insufficient_points_returns_409_not_500(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """點數餘額異常低於該筆累積（資料被外力改動）時，void 回 409 域錯誤、
    整筆回滾（銷售不被作廢）——不可冒出 500（Codex P2）。"""
    token, store_id = await _seed(db_session)
    member = await _seed_member(db_session, store_id)
    cat = await _seed_catalog(db_session, store_id, price="800")
    sale = await _checkout(client, token, cat, buyer=member.id)
    assert sale.status_code == 201
    from tests.integration.test_sales_api import _seed_manager

    # 先建並 commit 操作者（manager）：認證已回 DB 覆核（D-4），故 void 失敗時的「整筆回滾」
    # 只應回退竄改與作廢本身，不可連帶清掉操作者帳號（否則回滾後的 check 會 401）。
    mgr_token = await _seed_manager(db_session, store_id)
    await db_session.commit()
    member.member_points = 3  # 外力改動：低於該筆累積的 8 點（仍在 savepoint 內，會被 void 回滾）
    await db_session.flush()
    void = await client.post(
        f"/api/v1/sales/{sale.json()['id']}/void",
        headers={"Authorization": f"Bearer {mgr_token}"},
    )
    assert void.status_code == 409
    check = await client.get(
        f"/api/v1/sales/{sale.json()['id']}",
        headers={"Authorization": f"Bearer {mgr_token}"},
    )
    assert check.json()["invoice_status"] != "VOID"  # 整筆回滾、未作廢
    await db_session.refresh(member)
    # rollback 連同本測試的手動竄改（3）一起回復到累積後的 8——同一交易整筆回滾、
    # 無半套：點數絕不會落在被扣到負的狀態。
    assert member.member_points == 8


async def test_contact_create_rejects_negative_points(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """根因防護：建檔不可帶負點數（Codex P2 的可達路徑）。"""
    token, _store_id = await _seed(db_session)
    resp = await client.post(
        "/api/v1/contacts",
        json={"name": "負點數", "phone": "0911999888", "member_points": -1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_void_without_buyer_is_fine(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="800")
    sale = await _checkout(client, token, cat)
    from tests.integration.test_sales_api import _seed_manager

    mgr_token = await _seed_manager(db_session, store_id)
    void = await client.post(
        f"/api/v1/sales/{sale.json()['id']}/void",
        headers={"Authorization": f"Bearer {mgr_token}"},
    )
    assert void.status_code == 200
