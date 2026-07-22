"""SC-5b 當日溢價建議端點整合測試（docs/16 §6.2）：冷啟動、lazy 計算、冪等落庫、權限。"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.store.models import Store
from app.modules.storecredit.models import StoreCreditSuggestionLog
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import StoreCreditSourceType, UserRole

SUGGEST_URL = "/api/v1/store-credit/premium-suggestion/today"


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


async def _seed(session: AsyncSession) -> tuple[str, str, int, int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    member = Contact(store_id=store.id, name="會員甲", roles=["MEMBER"])
    session.add_all([mgr, clerk, member])
    await session.flush()
    mgr_token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
    clerk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return mgr_token, clerk_token, store.id, member.id, mgr.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_cold_start_returns_default_flagged(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store_id, _member_id, _mgr_id = await _seed(db_session)
    resp = await client.get(SUGGEST_URL, headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["suggested_rate"] == "0.1000"
    assert body["insufficient_data"] is True
    assert body["engine_version"]
    assert body["constraint_values"]["reason"] == "資料不足，採用預設值"


async def test_clerk_can_read_for_pos_panel(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """POS 開帳面板（店員）也要能看當日建議值（docs/16 §6.2）。"""
    _mgr, clerk, _store_id, _member_id, _mgr_id = await _seed(db_session)
    resp = await client.get(SUGGEST_URL, headers=_auth(clerk))
    assert resp.status_code == 200, resp.text


async def test_today_uses_taipei_calendar_date(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr, _clerk, _store_id, _member_id, _mgr_id = await _seed(db_session)
    monkeypatch.setattr(
        "app.modules.storecredit.router.utc_now",
        lambda: datetime(2026, 7, 21, 16, 30, tzinfo=UTC),  # 台灣 07-22 00:30
    )

    resp = await client.get(SUGGEST_URL, headers=_auth(mgr))

    assert resp.status_code == 200, resp.text
    assert resp.json()["for_date"] == "2026-07-22"


async def test_lazy_compute_and_idempotent_log(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """關閉冷啟動門檻後計算 → 落一筆當日 log；重打同日只一列（冪等）。"""
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    # 關閉冷啟動門檻（from_mapping 以預設補其餘鍵），讓引擎進入計算路徑。
    patched = await client.patch(
        "/api/v1/settings",
        json={"store_credit_engine_params": {"cold_start_min_days": 0}},
        headers=_auth(mgr),
    )
    assert patched.status_code == 200, patched.text
    # 一筆 CREDIT 讓帳本有資料（無約束綁定 → 建議＝現值 10%）。
    await StoreCreditService(db_session).credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.1"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        created_by=mgr_id,
    )

    first = await client.get(SUGGEST_URL, headers=_auth(mgr))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["insufficient_data"] is False
    assert body["suggested_rate"] == "0.1000"  # 無約束綁定 → 維持現值
    assert "combined" in body["window_metrics"]
    assert "windows" in body["window_metrics"]
    assert set(body["window_metrics"]["windows"]) == {"yesterday", "d7", "d30", "d90", "yoy"}

    second = await client.get(SUGGEST_URL, headers=_auth(mgr))
    assert second.status_code == 200
    assert second.json()["for_date"] == body["for_date"]

    count = await db_session.scalar(
        select(func.count())
        .select_from(StoreCreditSuggestionLog)
        .where(StoreCreditSuggestionLog.store_id == store_id)
    )
    assert count == 1  # lazy 落庫冪等：同日只一列
