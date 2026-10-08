"""手沖體驗卡（docs/63 §4、M1c）：引用既有品項＋預選選項，不另存價格或庫存。

管理者可新增／修改／刪除，店員只能讀；預選選項必須掛在該品項上、每個群組不超過可選上限；
跨店、封存品項一律找不到。發佈時只帶公開欄位（不含成本、不含店內 id 命名）。
"""

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
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

BASE = "/api/v1/online-order/experiences"


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


async def seed(session: AsyncSession, name: str = "露坑") -> tuple[str, str]:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    users = [
        User(store_id=store.id, username=f"{name}-{role.value}", password_hash="h", role=role)
        for role in (UserRole.CLERK, UserRole.MANAGER)
    ]
    session.add_all(users)
    await session.flush()
    return (
        encode_access_token(user_id=users[0].id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=users[1].id, role="MANAGER", store_id=store.id),
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def brew_menu(client: httpx.AsyncClient, manager: str) -> dict[str, int]:
    """手沖咖啡（掛「豆子」必選 1、「溫度」必選 1）＋一個沒掛群組的蛋糕。"""
    ids: dict[str, int] = {}
    item = await client.post(
        "/api/v1/menu-items",
        headers=auth(manager),
        json={"name": "手沖咖啡", "unit_price": "220", "unit_cost": "60", "category": "咖啡"},
    )
    assert item.status_code == 201, item.text
    ids["brew"] = item.json()["id"]
    cake = await client.post(
        "/api/v1/menu-items",
        headers=auth(manager),
        json={"name": "戚風", "unit_price": "90", "unit_cost": "30", "category": "甜點"},
    )
    ids["cake"] = cake.json()["id"]
    beans = await client.post(
        "/api/v1/menu-option-groups",
        headers=auth(manager),
        json={
            "name": "豆子",
            "min_select": 1,
            "max_select": 1,
            "options": [
                {"name": "蜜桃蹦蹦", "price_delta": "60"},
                {"name": "天堂鳥莊園", "price_delta": "20"},
            ],
        },
    )
    assert beans.status_code == 201, beans.text
    temp = await client.post(
        "/api/v1/menu-option-groups",
        headers=auth(manager),
        json={
            "name": "溫度",
            "min_select": 1,
            "max_select": 1,
            "options": [{"name": "熱"}, {"name": "冰"}],
        },
    )
    ids["peach"] = beans.json()["options"][0]["id"]
    ids["paradise"] = beans.json()["options"][1]["id"]
    ids["hot"] = temp.json()["options"][0]["id"]
    ids["beans_group"] = beans.json()["id"]
    ids["temp_group"] = temp.json()["id"]
    attached = await client.put(
        f"/api/v1/menu-items/{ids['brew']}/option-groups",
        headers=auth(manager),
        json={"group_ids": [ids["beans_group"], ids["temp_group"]]},
    )
    assert attached.status_code == 200, attached.text
    return ids


def card(ids: dict[str, int], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "menu_item_id": ids["brew"],
        "option_ids": [ids["peach"]],
        "title": "蜜桃蹦蹦手沖體驗",
        "tag": "清甜果香",
        "origin": "柯契爾｜水洗",
        "notes": "水蜜桃・白桃・荔枝・柚子醬・花香",
        "description": "以飽滿的水蜜桃與白桃甜香為主。",
        "includes": [
            {"title": "咖啡豆", "detail": "這支豆子現磨、單杯份量"},
            {"title": "完整器材使用", "detail": None},
        ],
        "theme": "peach",
        "art": "peach",
        "effect": "random",
        "is_active": True,
        "sort_order": 1,
    }
    body.update(overrides)
    return body


async def test_experience_crud_roundtrip_and_audit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    created = await client.post(BASE, headers=auth(manager), json=card(ids))
    assert created.status_code == 201, created.text
    exp_id = created.json()["id"]
    assert created.json() == {"id": exp_id, **card(ids)}

    listed = await client.get(BASE, headers=auth(clerk))
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [exp_id]

    changed = card(ids, title="天堂鳥手沖體驗", option_ids=[ids["paradise"], ids["hot"]])
    updated = await client.put(f"{BASE}/{exp_id}", headers=auth(manager), json=changed)
    assert updated.status_code == 200, updated.text
    assert updated.json() == {"id": exp_id, **changed}

    deleted = await client.delete(f"{BASE}/{exp_id}", headers=auth(manager))
    assert deleted.status_code == 204, deleted.text
    assert (await client.get(BASE, headers=auth(clerk))).json() == []

    actions = [
        row.action
        for row in await db_session.scalars(
            select(AuditLog).where(AuditLog.entity_type == "online_menu_experience")
        )
    ]
    assert sorted(actions) == sorted(
        [
            "CREATE_ONLINE_MENU_EXPERIENCE",
            "UPDATE_ONLINE_MENU_EXPERIENCE",
            "DELETE_ONLINE_MENU_EXPERIENCE",
        ]
    )


async def test_clerk_cannot_write_experiences(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    assert (await client.post(BASE, headers=auth(clerk), json=card(ids))).status_code == 403


@pytest.mark.parametrize("case", ["other_item", "over_group_limit", "duplicate", "unknown"])
async def test_preset_options_must_belong_to_the_item_and_respect_group_limits(
    client: httpx.AsyncClient, db_session: AsyncSession, case: str
) -> None:
    _, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    body = {
        # 選項掛在別的品項上（蛋糕沒有豆子群組）
        "other_item": card(ids, menu_item_id=ids["cake"]),
        # 同一個群組選超過上限（豆子只能選 1）
        "over_group_limit": card(ids, option_ids=[ids["peach"], ids["paradise"]]),
        "duplicate": card(ids, option_ids=[ids["peach"], ids["peach"]]),
        "unknown": card(ids, option_ids=[999_999]),
    }[case]
    resp = await client.post(BASE, headers=auth(manager), json=body)
    assert resp.status_code == 422, resp.text


async def test_card_fields_are_validated(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    many = [{"title": f"項目{i}", "detail": None} for i in range(6)]
    for body in (card(ids, includes=many), card(ids, theme="neon"), card(ids, effect="explode")):
        assert (await client.post(BASE, headers=auth(manager), json=body)).status_code == 422
    assert (await client.get(BASE, headers=auth(manager))).json() == []


async def test_other_store_cannot_see_experiences(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    assert (await client.post(BASE, headers=auth(manager), json=card(ids))).status_code == 201
    other_clerk, _ = await seed(db_session, "別家")
    assert (await client.get(BASE, headers=auth(other_clerk))).json() == []


@pytest.mark.parametrize("action", ["put", "delete", "create"])
async def test_other_store_cannot_touch_experiences_or_reference_items(
    client: httpx.AsyncClient, db_session: AsyncSession, action: str
) -> None:
    """（每個測試只送一個會失敗的請求：失敗時路由會 rollback，整個測試交易一起退回。）"""
    _, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    exp_id = (await client.post(BASE, headers=auth(manager), json=card(ids))).json()["id"]
    _, other_manager = await seed(db_session, "別家")
    if action == "put":
        resp = await client.put(f"{BASE}/{exp_id}", headers=auth(other_manager), json=card(ids))
    elif action == "delete":
        resp = await client.delete(f"{BASE}/{exp_id}", headers=auth(other_manager))
    else:  # 別家店不能引用這家店的品項
        resp = await client.post(BASE, headers=auth(other_manager), json=card(ids))
    assert resp.status_code == 404, resp.text


async def test_archived_item_cannot_get_an_experience(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    archived = await client.delete(f"/api/v1/menu-items/{ids['cake']}", headers=auth(manager))
    assert archived.status_code in (200, 204), archived.text
    gone = await client.post(
        BASE, headers=auth(manager), json=card(ids, menu_item_id=ids["cake"], option_ids=[])
    )
    assert gone.status_code == 404


async def test_presentation_role_roundtrip(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    path = f"/api/v1/online-order/menu-items/{ids['cake']}/presentation"
    assert (await client.get(path, headers=auth(manager))).json()["role"] is None
    saved = await client.put(path, headers=auth(manager), json={"role": "dessert"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["role"] == "dessert"
    bad = await client.put(path, headers=auth(manager), json={"role": "snack"})
    assert bad.status_code == 422
