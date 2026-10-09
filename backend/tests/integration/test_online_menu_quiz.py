"""「不知道喝什麼」引導推薦（docs/63 §2 M2a；店主 2026-10-09 裁示）。

題目 1–3 題、每題 2–4 個答案，每個答案勾「適合的品項」（菜單品項或手沖體驗卡）。
還沒存過時回一版預設題目（不啟用、沒勾品項）；管理者可改，店員只能讀；改了寫稽核。
"""

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.main import create_app
from tests.integration.test_online_menu_experiences import auth, brew_menu, card, seed

QUIZ = "/api/v1/online-order/quiz"


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


def quiz(*options_items: list[dict[str, Any]], active: bool = True) -> dict[str, Any]:
    """一題、每個答案依序掛上給的品項。"""
    return {
        "is_active": active,
        "questions": [
            {
                "prompt": "今天想來點什麼？",
                "options": [
                    {"label": f"答案{n}", "items": items} for n, items in enumerate(options_items)
                ],
            }
        ],
    }


async def test_default_questions_before_anything_is_saved(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, _ = await seed(db_session)
    resp = await client.get(QUIZ, headers=auth(clerk))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_default"] is True and body["is_active"] is False
    assert len(body["questions"]) == 3
    for question in body["questions"]:
        assert 2 <= len(question["options"]) <= 4
        assert all(option["items"] == [] for option in question["options"])


async def test_manager_saves_quiz_and_it_is_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    exp = await client.post(
        "/api/v1/online-order/experiences", json=card(ids), headers=auth(manager)
    )
    assert exp.status_code == 201, exp.text
    body = quiz(
        [{"kind": "item", "id": ids["brew"]}, {"kind": "experience", "id": exp.json()["id"]}],
        [{"kind": "item", "id": ids["cake"]}],
    )

    assert (await client.put(QUIZ, json=body, headers=auth(clerk))).status_code == 403
    saved = await client.put(QUIZ, json=body, headers=auth(manager))
    assert saved.status_code == 200, saved.text
    read = (await client.get(QUIZ, headers=auth(clerk))).json()
    assert read == {**body, "is_default": False}

    log = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "UPDATE_ONLINE_MENU_QUIZ")
    )
    assert log is not None and log.after is not None
    assert log.after["questions"][0]["options"][1]["items"] == [{"kind": "item", "id": ids["cake"]}]


async def test_references_must_be_this_stores_live_items(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    _, other_manager = await seed(db_session, name="別家")
    foreign = await brew_menu(client, other_manager)
    archived = await client.delete(f"/api/v1/menu-items/{ids['cake']}", headers=auth(manager))
    assert archived.status_code == 200, archived.text

    for refs in (
        [{"kind": "item", "id": foreign["brew"]}],
        [{"kind": "item", "id": ids["cake"]}],
        [{"kind": "experience", "id": 999_999}],
        [{"kind": "item", "id": ids["brew"]}, {"kind": "item", "id": ids["brew"]}],
    ):
        resp = await client.put(QUIZ, json=quiz(refs, []), headers=auth(manager))
        assert resp.status_code == 422, (refs, resp.text)


async def test_shape_limits(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, manager = await seed(db_session)
    question = {"prompt": "想喝什麼？", "options": [{"label": "咖啡", "items": []}] * 2}
    bad: list[dict[str, Any]] = [
        {"is_active": True, "questions": []},
        {"is_active": True, "questions": [question] * 4},
        {"is_active": True, "questions": [{**question, "options": question["options"][:1]}]},
        {"is_active": True, "questions": [{**question, "options": question["options"] * 3}]},
        {"is_active": True, "questions": [{**question, "prompt": "長" * 31}]},
        {
            "is_active": True,
            "questions": [{**question, "options": [{"label": "長" * 21, "items": []}] * 2}],
        },
    ]
    for body in bad:
        resp = await client.put(QUIZ, json=body, headers=auth(manager))
        assert resp.status_code == 422, body
