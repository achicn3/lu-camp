"""開店前檢查（2026-09-17 裁示）。

每天開店前該做的事一次列出來，全部綠燈才算完成。四項裁示：
1. 未完成**不擋**其他頁面——只有每天第一次進系統會自動帶到那頁，導覽列留紅點。
2. 自動項目（今日已開帳、各裝置連線）沒過時**可以略過，不必填原因**。
3. 狀態以「每店每日」為單位：任何一台裝置完成就算完成（店裡有收銀電腦＋手機＋平板）。
4. 自訂項目由店主在設定頁增減。

**裝置狀態不在後端**：hardware-agent 跑在店內電腦上，前端本來就直接問它（印標籤同一條路），
後端只管「開帳」「自訂項目」「今天做到哪」。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
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


async def _seed(session: AsyncSession) -> tuple[str, str, int, int]:
    """建店＋經理＋店員，回 (mgr_token, clerk_token, store_id, clerk_id)。未開帳。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([mgr, clerk])
    await session.flush()
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        store.id,
        clerk.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_today_reports_cash_session_state(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """今日已開帳與否由系統判定，店員不能自己打勾。"""
    mgr, _, store_id, clerk_id = await _seed(db_session)

    before = await client.get("/api/v1/opening-check/today", headers=_auth(mgr))
    assert before.status_code == 200, before.text
    assert before.json()["cash_session_open"] is False
    assert before.json()["completed"] is False

    await CashDrawerService(db_session).open_session(store_id, clerk_id, Decimal(1000))
    await db_session.flush()

    after = await client.get("/api/v1/opening-check/today", headers=_auth(mgr))
    assert after.json()["cash_session_open"] is True


async def test_manual_items_are_per_store_and_editable_by_manager(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, _store_id, _clerk_id = await _seed(db_session)

    created = await client.post(
        "/api/v1/opening-check/items",
        json={"label": "零錢補足", "href": "/cash"},
        headers=_auth(mgr),
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    listed = await client.get("/api/v1/opening-check/today", headers=_auth(clerk))
    assert [item["label"] for item in listed.json()["items"]] == ["零錢補足"]

    # 店員不可增刪項目（那是設定）
    assert (
        await client.post(
            "/api/v1/opening-check/items", json={"label": "偷加"}, headers=_auth(clerk)
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/opening-check/items/{item_id}", headers=_auth(clerk))
    ).status_code == 403


async def test_clerk_can_tick_items_and_completion_is_shared(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """打勾是店員的日常操作；狀態每店每日共用，換一台裝置看到的是同一份。"""
    mgr, clerk, store_id, clerk_id = await _seed(db_session)
    await CashDrawerService(db_session).open_session(store_id, clerk_id, Decimal(1000))
    await db_session.flush()
    item_id = (
        await client.post(
            "/api/v1/opening-check/items", json={"label": "零錢補足"}, headers=_auth(mgr)
        )
    ).json()["id"]

    ticked = await client.post(
        f"/api/v1/opening-check/today/items/{item_id}",
        json={"done": True},
        headers=_auth(clerk),
    )
    assert ticked.status_code == 200, ticked.text
    assert ticked.json()["completed"] is True  # 開帳已完成＋唯一的自訂項目已勾

    # 另一台裝置（這裡用經理的 token 代表另一個登入）看到同一份狀態
    other = await client.get("/api/v1/opening-check/today", headers=_auth(mgr))
    assert other.json()["items"][0]["done"] is True
    assert other.json()["completed"] is True

    # 取消勾選也要可以（按錯了）
    unticked = await client.post(
        f"/api/v1/opening-check/today/items/{item_id}",
        json={"done": False},
        headers=_auth(clerk),
    )
    assert unticked.json()["completed"] is False


async def test_skipping_auto_check_completes_without_reason(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """自動項目沒過可以略過、不必填原因（裁示）；略過只算今天。"""
    mgr, _, _store_id, _clerk_id = await _seed(db_session)

    resp = await client.post(
        "/api/v1/opening-check/today/skip", json={"key": "cash_session"}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["skipped_keys"] == ["cash_session"]
    assert resp.json()["completed"] is True  # 沒有自訂項目，開帳被略過 → 今天完成

    today = await client.get("/api/v1/opening-check/today", headers=_auth(mgr))
    assert today.json()["completed"] is True


async def test_device_keys_can_be_skipped_too(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """裝置狀態由前端問 hardware-agent，但「今天略過哪台」要跟其他裝置共用，所以存後端。"""
    mgr, _, _store_id, _clerk_id = await _seed(db_session)
    resp = await client.post(
        "/api/v1/opening-check/today/skip",
        json={"key": "device:LABEL_PRINTER:ql810w"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    assert "device:LABEL_PRINTER:ql810w" in resp.json()["skipped_keys"]


async def test_deleted_item_disappears_from_today(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """刪掉的自訂項目不該還卡著今天的完成狀態。"""
    mgr, _, store_id, clerk_id = await _seed(db_session)
    await CashDrawerService(db_session).open_session(store_id, clerk_id, Decimal(1000))
    await db_session.flush()
    item_id = (
        await client.post(
            "/api/v1/opening-check/items", json={"label": "冰箱溫度"}, headers=_auth(mgr)
        )
    ).json()["id"]
    assert (await client.get("/api/v1/opening-check/today", headers=_auth(mgr))).json()[
        "completed"
    ] is False

    deleted = await client.delete(f"/api/v1/opening-check/items/{item_id}", headers=_auth(mgr))
    assert deleted.status_code == 204, deleted.text
    today = await client.get("/api/v1/opening-check/today", headers=_auth(mgr))
    assert today.json()["items"] == []
    assert today.json()["completed"] is True


async def test_other_store_is_isolated(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _, _store_id, _clerk_id = await _seed(db_session)
    other = Store(name="別家店")
    db_session.add(other)
    await db_session.flush()
    other_mgr = User(
        store_id=other.id, username="mgr-other", password_hash="h", role=UserRole.MANAGER
    )
    db_session.add(other_mgr)
    await db_session.flush()
    other_token = encode_access_token(user_id=other_mgr.id, role="MANAGER", store_id=other.id)

    await client.post("/api/v1/opening-check/items", json={"label": "只有本店"}, headers=_auth(mgr))
    listed = await client.get("/api/v1/opening-check/today", headers=_auth(other_token))
    assert listed.json()["items"] == []


async def test_yesterdays_unclosed_session_is_not_today_s_opening(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """昨天忘記關帳，今天不算「已開帳」——那正是這個檢查該攔的錯。

    只看「有沒有 OPEN 的班別」會把昨天的班別當成今天開好了：今天的現金收入會被算進
    昨天的班別，對帳永遠對不平（CLAUDE.md §7 不變量 4）。狀態要分成三種，訊息也要
    直接告訴店員「先把昨天的帳結掉」。
    """
    from datetime import timedelta

    from sqlalchemy import text

    mgr, _, store_id, clerk_id = await _seed(db_session)
    await CashDrawerService(db_session).open_session(store_id, clerk_id, Decimal(1000))
    await db_session.flush()
    # 把開帳時間挪到昨天（班別仍是 OPEN）
    await db_session.execute(
        text("UPDATE cash_sessions SET opened_at = :t WHERE store_id = :s"),
        {"t": datetime.now(UTC) - timedelta(days=1), "s": store_id},
    )
    await db_session.flush()

    today = await client.get("/api/v1/opening-check/today", headers=_auth(mgr))
    assert today.status_code == 200, today.text
    assert today.json()["cash_session_state"] == "STALE"
    assert today.json()["completed"] is False


async def test_reading_today_does_not_write(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """GET 是唯讀：這支 query 掛在每一頁，第一個早晨兩台同時開頁不該互撞唯一鍵。"""
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.modules.openingcheck.models import OpeningCheck

    mgr, _, _store_id, _clerk_id = await _seed(db_session)
    await client.get("/api/v1/opening-check/today", headers=_auth(mgr))
    rows = await db_session.scalar(sa_select(func.count()).select_from(OpeningCheck))
    assert rows == 0  # 沒有人打勾/略過之前，不該留下任何一列


async def test_item_changes_are_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """自訂項目是 MANAGER-only 的設定變更，增刪都要留稽核（§5）。"""
    from sqlalchemy import select as sa_select

    from app.core.audit import AuditLog

    mgr, _, _store_id, _clerk_id = await _seed(db_session)
    item_id = (
        await client.post(
            "/api/v1/opening-check/items", json={"label": "招牌燈"}, headers=_auth(mgr)
        )
    ).json()["id"]
    await client.delete(f"/api/v1/opening-check/items/{item_id}", headers=_auth(mgr))

    logs = (
        await db_session.scalars(
            sa_select(AuditLog)
            .where(AuditLog.action.like("%OPENING_CHECK_ITEM%"))
            .order_by(AuditLog.id)
        )
    ).all()
    assert [log.action for log in logs] == [
        "CREATE_OPENING_CHECK_ITEM",
        "DELETE_OPENING_CHECK_ITEM",
    ]


async def test_skip_can_be_undone(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """略過按錯要能取消：打勾可以取消，略過沒道理只能等明天。"""
    mgr, _, _store_id, _clerk_id = await _seed(db_session)
    await client.post(
        "/api/v1/opening-check/today/skip", json={"key": "cash_session"}, headers=_auth(mgr)
    )
    undone = await client.post(
        "/api/v1/opening-check/today/skip",
        json={"key": "cash_session", "skipped": False},
        headers=_auth(mgr),
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["skipped_keys"] == []
    assert undone.json()["completed"] is False


async def test_skip_rejects_unknown_key(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """key 打錯會靜默存進陣列、永遠不生效；只收得認得的兩種。"""
    mgr, _, _store_id, _clerk_id = await _seed(db_session)
    resp = await client.post(
        "/api/v1/opening-check/today/skip", json={"key": "隨便打的"}, headers=_auth(mgr)
    )
    assert resp.status_code == 422, resp.text


async def test_item_href_rejects_protocol_relative(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`//evil.com` 與 `/\\evil.com` 也是以 / 開頭，卻會把店員導到站外。"""
    mgr, _, _store_id, _clerk_id = await _seed(db_session)
    for bad in ("//evil.com", "/\\evil.com"):
        resp = await client.post(
            "/api/v1/opening-check/items",
            json={"label": "外部連結", "href": bad},
            headers=_auth(mgr),
        )
        assert resp.status_code == 422, (bad, resp.text)


async def test_item_href_must_be_internal_path(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """連結只收站內路徑：這個值會直接餵給站內導覽。"""
    mgr, _, _store_id, _clerk_id = await _seed(db_session)
    resp = await client.post(
        "/api/v1/opening-check/items",
        json={"label": "外部連結", "href": "https://example.com"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 422, resp.text
