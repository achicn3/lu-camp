"""彈性菜單 API 整合測試（docs/44 §3）：分類、選項群組、選項、品項掛群組。

群組可被多個品項共用；品項讀取時帶出所掛群組與其未封存選項，POS 與線上點餐共用。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
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


async def _seed(session: AsyncSession, name: str = "門市") -> tuple[str, str]:
    """建店+店員+經理，回 (clerk_token, manager_token)。"""
    store = Store(name=name)
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username=f"clk-{name}", password_hash="h", role=UserRole.CLERK)
    mgr = User(store_id=store.id, username=f"mgr-{name}", password_hash="h", role=UserRole.MANAGER)
    session.add_all([clerk, mgr])
    await session.flush()
    return (
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _group(
    client: httpx.AsyncClient,
    token: str,
    name: str,
    options: list[tuple[str, str]],
    *,
    min_select: int = 1,
    max_select: int = 1,
) -> dict[str, object]:
    resp = await client.post(
        "/api/v1/menu-option-groups",
        json={
            "name": name,
            "min_select": min_select,
            "max_select": max_select,
            "options": [{"name": n, "price_delta": d} for n, d in options],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, object] = resp.json()
    return body


async def _item(client: httpx.AsyncClient, token: str, name: str, price: str = "120") -> int:
    resp = await client.post(
        "/api/v1/menu-items", json={"name": name, "unit_price": price}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    item_id: int = resp.json()["id"]
    return item_id


# ── 分類 ──


async def test_item_category_name_reuses_one_category_row(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """建品項時給分類名稱：同名沿用同一筆分類，不重複建。"""
    _, mgr = await _seed(db_session)
    for name in ("美式", "拿鐵"):
        resp = await client.post(
            "/api/v1/menu-items",
            json={"name": name, "unit_price": "120", "category": "咖啡"},
            headers=_auth(mgr),
        )
        assert resp.status_code == 201, resp.text

    cats = await client.get("/api/v1/menu-categories", headers=_auth(mgr))
    assert cats.status_code == 200
    assert [c["name"] for c in cats.json()] == ["咖啡"]
    items = (await client.get("/api/v1/menu-items", headers=_auth(mgr))).json()
    assert {i["category"] for i in items} == {"咖啡"}
    assert len({i["category_id"] for i in items}) == 1


async def test_rename_category_applies_to_all_items(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    await client.post(
        "/api/v1/menu-items",
        json={"name": "美式", "unit_price": "120", "category": "咖啡"},
        headers=_auth(mgr),
    )
    cat_id = (await client.get("/api/v1/menu-categories", headers=_auth(mgr))).json()[0]["id"]
    resp = await client.patch(
        f"/api/v1/menu-categories/{cat_id}",
        json={"name": "店員沖", "sort_order": 3},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sort_order"] == 3
    items = (await client.get("/api/v1/menu-items", headers=_auth(mgr))).json()
    assert items[0]["category"] == "店員沖"


async def test_categories_are_store_scoped(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr_a = await _seed(db_session, "A")
    _, mgr_b = await _seed(db_session, "B")
    await client.post("/api/v1/menu-categories", json={"name": "甜點"}, headers=_auth(mgr_a))
    assert (await client.get("/api/v1/menu-categories", headers=_auth(mgr_b))).json() == []


# ── 選項群組 ──


async def test_create_option_group_with_options(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    group = await _group(client, mgr, "溫度", [("熱", "0"), ("冰", "0")])
    assert group["name"] == "溫度"
    assert group["min_select"] == 1
    assert group["max_select"] == 1
    options = group["options"]
    assert isinstance(options, list)
    assert [(o["name"], o["price_delta"], o["is_available"]) for o in options] == [
        ("熱", "0", True),
        ("冰", "0", True),
    ]


async def test_option_group_rejects_bad_bounds(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    for lo, hi in ((2, 1), (0, 0), (-1, 1)):
        resp = await client.post(
            "/api/v1/menu-option-groups",
            json={"name": f"x{lo}{hi}", "min_select": lo, "max_select": hi, "options": []},
            headers=_auth(mgr),
        )
        assert resp.status_code == 422, (lo, hi, resp.text)


async def test_option_rejects_negative_or_fractional_price_delta(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    for delta in ("-5", "10.5"):
        resp = await client.post(
            "/api/v1/menu-option-groups",
            json={
                "name": "加購",
                "min_select": 0,
                "max_select": 3,
                "options": [{"name": "燕麥奶", "price_delta": delta}],
            },
            headers=_auth(mgr),
        )
        assert resp.status_code == 422, (delta, resp.text)


async def test_clerk_can_read_but_not_write_option_groups(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr = await _seed(db_session)
    await _group(client, mgr, "溫度", [("熱", "0")])
    assert (await client.get("/api/v1/menu-option-groups", headers=_auth(clerk))).status_code == 200
    resp = await client.post(
        "/api/v1/menu-option-groups",
        json={"name": "豆種", "min_select": 1, "max_select": 1, "options": []},
        headers=_auth(clerk),
    )
    assert resp.status_code == 403


async def test_duplicate_group_name_conflicts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    await _group(client, mgr, "溫度", [("熱", "0")])
    resp = await client.post(
        "/api/v1/menu-option-groups",
        json={"name": "溫度", "min_select": 1, "max_select": 1, "options": []},
        headers=_auth(mgr),
    )
    assert resp.status_code == 409


async def test_add_update_and_archive_option(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    group = await _group(client, mgr, "加購", [("燕麥奶", "20")], min_select=0, max_select=2)
    added = await client.post(
        f"/api/v1/menu-option-groups/{group['id']}/options",
        json={"name": "濃縮加一份", "price_delta": "30"},
        headers=_auth(mgr),
    )
    assert added.status_code == 201, added.text
    opt_id = added.json()["id"]

    patched = await client.patch(
        f"/api/v1/menu-options/{opt_id}",
        json={"price_delta": "35", "is_available": False},
        headers=_auth(mgr),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["price_delta"] == "35"
    assert patched.json()["is_available"] is False

    archived = await client.delete(f"/api/v1/menu-options/{opt_id}", headers=_auth(mgr))
    assert archived.status_code == 200
    groups = (await client.get("/api/v1/menu-option-groups", headers=_auth(mgr))).json()
    assert [o["name"] for o in groups[0]["options"]] == ["燕麥奶"]


async def test_option_price_change_is_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    group = await _group(client, mgr, "加購", [("燕麥奶", "20")], min_select=0, max_select=2)
    options = group["options"]
    assert isinstance(options, list)
    opt_id = options[0]["id"]
    await client.patch(
        f"/api/v1/menu-options/{opt_id}", json={"price_delta": "25"}, headers=_auth(mgr)
    )
    log = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "UPDATE_MENU_OPTION_PRICE")
    )
    assert log is not None
    assert log.before == {"price_delta": "20"}
    assert log.after == {"price_delta": "25"}


# ── 品項掛群組 ──


async def test_attach_groups_to_item_in_order_and_share_across_items(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr = await _seed(db_session)
    temp = await _group(client, mgr, "溫度", [("熱", "0"), ("冰", "0")])
    milk = await _group(client, mgr, "奶", [("鮮奶", "0"), ("燕麥奶", "20")])
    latte = await _item(client, mgr, "拿鐵", "150")
    americano = await _item(client, mgr, "美式", "120")

    resp = await client.put(
        f"/api/v1/menu-items/{latte}/option-groups",
        json={"group_ids": [milk["id"], temp["id"]]},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    assert [g["name"] for g in resp.json()["option_groups"]] == ["奶", "溫度"]
    await client.put(
        f"/api/v1/menu-items/{americano}/option-groups",
        json={"group_ids": [temp["id"]]},
        headers=_auth(mgr),
    )

    items = {
        i["name"]: i for i in (await client.get("/api/v1/menu-items", headers=_auth(clerk))).json()
    }
    assert [g["name"] for g in items["拿鐵"]["option_groups"]] == ["奶", "溫度"]
    milk_opts = items["拿鐵"]["option_groups"][0]["options"]
    assert [(o["name"], o["price_delta"]) for o in milk_opts] == [("鮮奶", "0"), ("燕麥奶", "20")]
    assert [g["name"] for g in items["美式"]["option_groups"]] == ["溫度"]


async def test_reattach_replaces_previous_groups(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    temp = await _group(client, mgr, "溫度", [("熱", "0")])
    beans = await _group(client, mgr, "豆種", [("衣索比亞", "0")])
    item = await _item(client, mgr, "手沖")
    url = f"/api/v1/menu-items/{item}/option-groups"
    await client.put(url, json={"group_ids": [temp["id"]]}, headers=_auth(mgr))
    resp = await client.put(url, json={"group_ids": [beans["id"]]}, headers=_auth(mgr))
    assert [g["name"] for g in resp.json()["option_groups"]] == ["豆種"]
    resp = await client.put(url, json={"group_ids": []}, headers=_auth(mgr))
    assert resp.json()["option_groups"] == []


async def test_attach_rejects_duplicate_or_foreign_group(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr_a = await _seed(db_session, "A")
    _, mgr_b = await _seed(db_session, "B")
    foreign = await _group(client, mgr_b, "溫度", [("熱", "0")])
    own = await _group(client, mgr_a, "溫度", [("熱", "0")])
    item = await _item(client, mgr_a, "美式")
    url = f"/api/v1/menu-items/{item}/option-groups"
    resp = await client.put(url, json={"group_ids": [foreign["id"]]}, headers=_auth(mgr_a))
    assert resp.status_code == 404
    resp = await client.put(url, json={"group_ids": [own["id"], own["id"]]}, headers=_auth(mgr_a))
    assert resp.status_code == 422


async def test_archived_group_disappears_from_items(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    temp = await _group(client, mgr, "溫度", [("熱", "0")])
    item = await _item(client, mgr, "美式")
    await client.put(
        f"/api/v1/menu-items/{item}/option-groups",
        json={"group_ids": [temp["id"]]},
        headers=_auth(mgr),
    )
    resp = await client.delete(f"/api/v1/menu-option-groups/{temp['id']}", headers=_auth(mgr))
    assert resp.status_code == 200
    items = (await client.get("/api/v1/menu-items", headers=_auth(mgr))).json()
    assert items[0]["option_groups"] == []
    assert (await client.get("/api/v1/menu-option-groups", headers=_auth(mgr))).json() == []


async def test_item_description_round_trips(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    resp = await client.post(
        "/api/v1/menu-items",
        json={"name": "烏干達美式", "unit_price": "120", "description": "堅果、黑糖尾韻"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 201
    item_id = resp.json()["id"]
    assert resp.json()["description"] == "堅果、黑糖尾韻"
    resp = await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"description": None}, headers=_auth(mgr)
    )
    assert resp.json()["description"] is None


async def test_option_cost_via_api_set_and_clear(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """選項成本（docs/49 F1）：PATCH 可設、明確送 null 可清空、沒送不動。"""
    _, mgr = await _seed(db_session)
    group = await _group(client, mgr, "奶", [("燕麥奶", "20")])
    options = group["options"]
    assert isinstance(options, list)
    url = f"/api/v1/menu-options/{options[0]['id']}"
    resp = await client.patch(url, json={"unit_cost": "8"}, headers=_auth(mgr))
    assert resp.json()["unit_cost"] == "8"
    resp = await client.patch(url, json={"price_delta": "25"}, headers=_auth(mgr))
    assert resp.json()["unit_cost"] == "8"
    resp = await client.patch(url, json={"unit_cost": None}, headers=_auth(mgr))
    assert resp.json()["unit_cost"] is None
    assert (
        await client.patch(url, json={"unit_cost": "1.5"}, headers=_auth(mgr))
    ).status_code == 422
