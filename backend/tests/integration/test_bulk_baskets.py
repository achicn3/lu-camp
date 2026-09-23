"""散裝販售籃（ADR-025）：多次收購共用一張標籤，但每次收購的來源、成本各自保留。

情境：甲賣 10 支營釘（總成本 50）、乙後來又賣 20 支（總成本 160）。兩批放同一籃、
貼同一張標籤、每支同一個價；籃子顯示 30 支，但甲乙的收購單、成本與數量互不覆寫。
"""

import itertools
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import BulkLot
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

_seq = itertools.count()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _token(session: AsyncSession, role: UserRole = UserRole.MANAGER) -> tuple[int, str]:
    store = Store(name=f"散裝門市{next(_seq)}")
    session.add(store)
    await session.flush()
    return store.id, await _user_token(session, store.id, role)


async def _user_token(session: AsyncSession, store_id: int, role: UserRole) -> str:
    user = User(store_id=store_id, username=f"basket-{next(_seq)}", password_hash="h", role=role)
    session.add(user)
    await session.flush()
    return encode_access_token(user_id=user.id, role=role.value, store_id=store_id)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"bk-{next(_seq)}"}


async def _seller(client: httpx.AsyncClient, token: str) -> int:
    resp = await client.post(
        "/api/v1/contacts",
        json={
            "name": "賣家",
            "phone": f"09{next(_seq):08d}",
            "roles": ["SELLER"],
            "national_id": "A123456789",
        },
        headers=_h(token),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _open_drawer(client: httpx.AsyncClient, token: str) -> None:
    resp = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_h(token)
    )
    assert resp.status_code == 201, resp.text


async def _acquire_bulk(
    client: httpx.AsyncClient,
    token: str,
    *,
    qty: int,
    cost: str,
    price: str = "20",
    name: str = "無品牌營釘",
    **lot_extra: Any,
) -> dict[str, Any]:
    seller = await _seller(client, token)
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BULK_LOT",
            "contact_id": seller,
            "lot": {
                "name": name,
                "acquisition_cost": cost,
                "acquisition_basis": "UNSPECIFIED",
                "total_qty": qty,
                "unit_price": price,
                **lot_extra,
            },
        },
        headers=_h(token),
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def _lot(session: AsyncSession, lot_code: str) -> BulkLot:
    lot = await session.scalar(select(BulkLot).where(BulkLot.lot_code == lot_code))
    assert lot is not None
    await session.refresh(lot)
    return lot


async def test_create_empty_basket_readable_by_code_and_isolated_per_store(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, token = await _token(db_session)
    resp = await client.post(
        "/api/v1/bulk-baskets", headers=_h(token), json={"name": "無品牌營釘", "unit_price": "20"}
    )
    assert resp.status_code == 201, resp.text
    basket = resp.json()
    assert basket["code"].startswith("K")
    assert basket["remaining_qty"] == 0
    assert basket["sources"] == []
    assert basket["cost_reference"]["sample_count"] == 0

    by_code = await client.get(f"/api/v1/bulk-baskets/by-code/{basket['code']}", headers=_h(token))
    assert by_code.status_code == 200, by_code.text
    assert by_code.json()["id"] == basket["id"]

    _, other = await _token(db_session)
    assert (
        await client.get(f"/api/v1/bulk-baskets/{basket['id']}", headers=_h(other))
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/bulk-baskets/by-code/{basket['code']}", headers=_h(other))
    ).status_code == 404


async def test_two_acquisitions_share_one_basket_but_keep_their_own_cost_and_qty(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, token = await _token(db_session)
    await _open_drawer(client, token)

    first = await _acquire_bulk(client, token, qty=10, cost="50", new_basket=True)
    assert first["basket_code"] is not None
    basket = (
        await client.get(f"/api/v1/bulk-baskets/by-code/{first['basket_code']}", headers=_h(token))
    ).json()

    # 乙：只填數量與總成本；名稱、單價以籃子為準（即使前端送了別的值也不採用）。
    second = await _acquire_bulk(
        client, token, qty=20, cost="160", price="999", name="亂填", basket_id=basket["id"]
    )
    assert second["basket_code"] == first["basket_code"]

    lot_a = await _lot(db_session, first["lot_code"])
    lot_b = await _lot(db_session, second["lot_code"])
    assert (lot_a.basket_id, lot_b.basket_id) == (basket["id"], basket["id"])
    assert (lot_a.total_qty, lot_a.acquisition_cost) == (10, Decimal(50))
    assert (lot_b.total_qty, lot_b.acquisition_cost) == (20, Decimal(160))
    assert lot_b.unit_price == 20
    assert lot_b.name == "無品牌營釘"

    read = (await client.get(f"/api/v1/bulk-baskets/{basket['id']}", headers=_h(token))).json()
    assert read["remaining_qty"] == 30
    assert [s["lot_code"] for s in read["sources"]] == [first["lot_code"], second["lot_code"]]
    # 單件估價參考：甲 50÷10=5、乙 160÷20=8；樣本數＝來源收購筆數。
    assert read["cost_reference"] == {"sample_count": 2, "unit_cost_min": "5", "unit_cost_max": "8"}


async def test_old_lot_label_points_to_its_basket(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """舊來源標籤入籃後仍掃得到，並告訴 POS 它屬於哪一籃（POS 據此改賣整籃庫存）。"""
    _, token = await _token(db_session)
    await _open_drawer(client, token)
    first = await _acquire_bulk(client, token, qty=10, cost="50", new_basket=True)
    lot = await client.get(f"/api/v1/bulk-lots/by-code/{first['lot_code']}", headers=_h(token))
    assert lot.status_code == 200, lot.text
    assert lot.json()["basket_id"] is not None


async def test_cannot_join_basket_of_another_store_or_inactive(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, token = await _token(db_session)
    await _open_drawer(client, token)
    first = await _acquire_bulk(client, token, qty=10, cost="50", new_basket=True)
    basket_id = (
        await client.get(f"/api/v1/bulk-baskets/by-code/{first['basket_code']}", headers=_h(token))
    ).json()["id"]

    _, other = await _token(db_session)
    await _open_drawer(client, other)
    seller = await _seller(client, other)
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BULK_LOT",
            "contact_id": seller,
            "lot": {
                "name": "x",
                "acquisition_cost": "10",
                "acquisition_basis": "UNSPECIFIED",
                "total_qty": 1,
                "unit_price": "20",
                "basket_id": basket_id,
            },
        },
        headers=_h(other),
    )
    assert resp.status_code == 404, resp.text

    patched = await client.patch(
        f"/api/v1/bulk-baskets/{basket_id}", headers=_h(token), json={"is_active": False}
    )
    assert patched.status_code == 200, patched.text
    seller = await _seller(client, token)
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BULK_LOT",
            "contact_id": seller,
            "lot": {
                "name": "x",
                "acquisition_cost": "10",
                "acquisition_basis": "UNSPECIFIED",
                "total_qty": 1,
                "unit_price": "20",
                "basket_id": basket_id,
            },
        },
        headers=_h(token),
    )
    assert resp.status_code == 409, resp.text


async def test_new_basket_and_basket_id_are_mutually_exclusive(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, token = await _token(db_session)
    await _open_drawer(client, token)
    seller = await _seller(client, token)
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BULK_LOT",
            "contact_id": seller,
            "lot": {
                "name": "x",
                "acquisition_cost": "10",
                "acquisition_basis": "UNSPECIFIED",
                "total_qty": 1,
                "unit_price": "20",
                "basket_id": 1,
                "new_basket": True,
            },
        },
        headers=_h(token),
    )
    assert resp.status_code == 422, resp.text


async def test_basket_price_change_moves_every_source_and_is_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同籃一個價：改籃價即改所有來源的售價（含已售完、日後退貨會回來的那批）。"""
    _, token = await _token(db_session)
    await _open_drawer(client, token)
    first = await _acquire_bulk(client, token, qty=10, cost="50", new_basket=True)
    basket = (
        await client.get(f"/api/v1/bulk-baskets/by-code/{first['basket_code']}", headers=_h(token))
    ).json()
    second = await _acquire_bulk(client, token, qty=20, cost="160", basket_id=basket["id"])

    resp = await client.patch(
        f"/api/v1/bulk-baskets/{basket['id']}", headers=_h(token), json={"unit_price": "15"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["unit_price"] == "15"
    for code in (first["lot_code"], second["lot_code"]):
        assert (await _lot(db_session, code)).unit_price == 15

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.entity_type == "bulk_basket", AuditLog.entity_id == str(basket["id"])
        )
    )
    assert audit is not None


async def test_clerk_cannot_edit_basket(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, manager = await _token(db_session)
    clerk = await _user_token(db_session, store_id, UserRole.CLERK)
    basket = (
        await client.post(
            "/api/v1/bulk-baskets", headers=_h(manager), json={"name": "營釘", "unit_price": "20"}
        )
    ).json()
    resp = await client.patch(
        f"/api/v1/bulk-baskets/{basket['id']}", headers=_h(clerk), json={"unit_price": "1"}
    )
    assert resp.status_code == 403


async def test_source_in_basket_cannot_be_repriced_on_its_own(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """來源不能繞過籃子單獨改價，否則同一張標籤兩個價。"""
    _, token = await _token(db_session)
    await _open_drawer(client, token)
    first = await _acquire_bulk(client, token, qty=10, cost="50", new_basket=True)
    lot = await _lot(db_session, first["lot_code"])
    resp = await client.patch(
        f"/api/v1/bulk-lots/{lot.id}/price", headers=_h(token), json={"unit_price": "30"}
    )
    assert resp.status_code == 409, resp.text
    assert (await _lot(db_session, first["lot_code"])).unit_price == 20


async def test_existing_lot_can_be_added_only_when_same_price_and_owned(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """既有散裝（入籃功能上線前收的）整批加入籃：同價才合併（店主 2026-09-22 裁示）。"""
    _, token = await _token(db_session)
    await _open_drawer(client, token)
    basket = (
        await client.post(
            "/api/v1/bulk-baskets", headers=_h(token), json={"name": "營釘", "unit_price": "20"}
        )
    ).json()
    same = await _acquire_bulk(client, token, qty=5, cost="30")
    other_price = await _acquire_bulk(client, token, qty=5, cost="30", price="25")

    ok = await client.post(
        f"/api/v1/bulk-baskets/{basket['id']}/lots",
        headers=_h(token),
        json={"bulk_lot_id": (await _lot(db_session, same["lot_code"])).id},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["remaining_qty"] == 5

    rejected = await client.post(
        f"/api/v1/bulk-baskets/{basket['id']}/lots",
        headers=_h(token),
        json={"bulk_lot_id": (await _lot(db_session, other_price["lot_code"])).id},
    )
    assert rejected.status_code == 409, rejected.text

    again = await client.post(
        f"/api/v1/bulk-baskets/{basket['id']}/lots",
        headers=_h(token),
        json={"bulk_lot_id": (await _lot(db_session, same["lot_code"])).id},
    )
    assert again.status_code == 409, again.text


async def test_list_baskets_filters_by_name(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, token = await _token(db_session)
    for name in ("無品牌營釘", "營繩"):
        await client.post(
            "/api/v1/bulk-baskets", headers=_h(token), json={"name": name, "unit_price": "20"}
        )
    resp = await client.get("/api/v1/bulk-baskets", params={"q": "營釘"}, headers=_h(token))
    assert resp.status_code == 200, resp.text
    assert [b["name"] for b in resp.json()] == ["無品牌營釘"]


async def test_acquisition_replay_returns_same_basket_code(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """冪等重送必須回同一籃，不可再建一個新籃。"""
    _, token = await _token(db_session)
    await _open_drawer(client, token)
    seller = await _seller(client, token)
    body = {
        "type": "BULK_LOT",
        "contact_id": seller,
        "lot": {
            "name": "營釘",
            "acquisition_cost": "50",
            "acquisition_basis": "UNSPECIFIED",
            "total_qty": 10,
            "unit_price": "20",
            "new_basket": True,
        },
    }
    headers = _h(token)
    first = await client.post("/api/v1/acquisitions", json=body, headers=headers)
    replay = await client.post("/api/v1/acquisitions", json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert replay.status_code in (200, 201), replay.text
    assert replay.json()["basket_code"] == first.json()["basket_code"]
    listed = (await client.get("/api/v1/bulk-baskets", headers=_h(token))).json()
    assert len(listed) == 1


async def test_basket_exposes_each_source_note_for_checkout_reminders(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收購時寫在那批的備註（例：有 3 支彎掉）入籃後也要讀得到。

    POS 才能在結帳前提醒（Codex 第二輪）。
    """
    _, token = await _token(db_session)
    await _open_drawer(client, token)
    first = await _acquire_bulk(client, token, qty=10, cost="50", new_basket=True)
    basket = (
        await client.get(f"/api/v1/bulk-baskets/by-code/{first['basket_code']}", headers=_h(token))
    ).json()
    await _acquire_bulk(
        client, token, qty=20, cost="160", basket_id=basket["id"], note="有 3 支彎掉"
    )
    read = (await client.get(f"/api/v1/bulk-baskets/{basket['id']}", headers=_h(token))).json()
    assert [s["note"] for s in read["sources"]] == [None, "有 3 支彎掉"]
