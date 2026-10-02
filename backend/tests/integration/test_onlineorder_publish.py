"""發佈菜單到線上點餐（docs/44 §3.5、§4.1、§5.2；O3b）。

用假的 Worker（httpx.MockTransport）接住店內 backend 送出的請求：每個請求都要帶正確簽章，
順序是字型 → 照片 → 桌位 → 菜單；照片與字型推過就不再推；桌位碼依設定的桌號產生、可單桌重發。
"""

import hashlib
import hmac
import io
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest_asyncio
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.models import OnlineMenuPublication
from app.modules.onlineorder.router import get_online_order_client
from app.modules.onlineorder.signing import canonical_string
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

SECRET = "publish-test-secret"
BASE = "https://order.test"


@dataclass
class FakeWorker:
    """記下收到的請求並驗簽；fail_on 指定某路徑回 500（模擬雲端出錯）。"""

    calls: list[tuple[str, str, bytes]] = field(default_factory=list)
    stored: set[str] = field(default_factory=set)
    fail_on: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        text = canonical_string(
            request.method,
            request.url.raw_path.decode(),
            request.headers["X-LuCamp-Timestamp"],
            request.headers["X-LuCamp-Nonce"],
            body,
        )
        expected = hmac.new(SECRET.encode(), text.encode(), hashlib.sha256).hexdigest()
        assert request.headers["X-LuCamp-Signature"] == expected
        path = request.url.path
        self.calls.append((request.method, path, body))
        if self.fail_on is not None and path.startswith(self.fail_on):
            return httpx.Response(500, json={"error": "boom"})
        if path.startswith(("/integration/photos/", "/integration/fonts/")):
            if path in self.stored:
                return httpx.Response(204)
            self.stored.add(path)
            return httpx.Response(201)
        return httpx.Response(200, json={})

    def paths(self) -> list[str]:
        return [p for _, p, _ in self.calls]

    def menu(self) -> dict[str, Any]:
        body = [b for _, p, b in self.calls if p == "/integration/menu"][-1]
        result: dict[str, Any] = json.loads(body)
        return result

    def tables(self) -> list[dict[str, str]]:
        body = [b for _, p, b in self.calls if p == "/integration/tables"][-1]
        result: list[dict[str, str]] = json.loads(body)["tables"]
        return result


@pytest_asyncio.fixture
async def worker() -> FakeWorker:
    return FakeWorker()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, worker: FakeWorker) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    def _client() -> OnlineOrderClient:
        return OnlineOrderClient(
            BASE, SECRET, store_id=_STORE["id"], transport=httpx.MockTransport(worker.handler)
        )

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[get_online_order_client] = _client
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# 雲端綁定的店：_seed 建好後填入（client fixture 在請求時才讀）。
_STORE: dict[str, int] = {"id": 0}


async def _seed(session: AsyncSession) -> tuple[str, str]:
    store = Store(name="露坑")
    session.add(store)
    await session.flush()
    _STORE["id"] = store.id
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add_all([clerk, mgr])
    await session.flush()
    return (
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(buf, format="JPEG")
    return buf.getvalue()


async def _menu(client: httpx.AsyncClient, mgr: str) -> dict[str, int]:
    ids: dict[str, int] = {}
    for name, price, category in (
        ("拿鐵", "150", "咖啡"),
        ("戚風", "90", "甜點"),
        ("下架品", "80", "甜點"),
    ):
        resp = await client.post(
            "/api/v1/menu-items",
            json={"name": name, "unit_price": price, "unit_cost": "30", "category": category},
            headers=_auth(mgr),
        )
        assert resp.status_code == 201, resp.text
        ids[name] = resp.json()["id"]
    await client.patch(
        f"/api/v1/menu-items/{ids['下架品']}", json={"is_available": False}, headers=_auth(mgr)
    )
    photo = await client.post(
        f"/api/v1/menu-items/{ids['拿鐵']}/photo",
        files={"file": ("a.jpg", _jpeg((200, 150, 100)), "image/jpeg")},
        headers=_auth(mgr),
    )
    assert photo.status_code == 200, photo.text
    await client.patch(
        "/api/v1/settings",
        json={"dine_in_tables": ["A1", "A2"]},
        headers=_auth(mgr),
    )
    return ids


async def test_publish_pushes_font_photos_tables_then_menu(
    client: httpx.AsyncClient, db_session: AsyncSession, worker: FakeWorker
) -> None:
    _, mgr = await _seed(db_session)
    await _menu(client, mgr)
    resp = await client.post("/api/v1/online-order/publish", headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item_count"] == 2
    paths = worker.paths()
    assert paths[0].startswith("/integration/fonts/")
    assert paths[1].startswith("/integration/photos/")
    assert paths[2:] == ["/integration/tables", "/integration/menu"]

    menu = worker.menu()
    assert menu["version"] == body["version"]
    assert sorted(i["name"] for i in menu["items"]) == ["戚風", "拿鐵"]  # 下架品不出現
    assert "cost" not in json.dumps(menu)
    assert menu["font"] == paths[0].removeprefix("/integration/fonts/")

    tables = worker.tables()
    assert sorted((t["label"], t["service_mode"]) for t in tables) == [
        ("A1", "DINE_IN"),
        ("A2", "DINE_IN"),
        ("外帶", "TAKEOUT"),
    ]
    assert all(len(t["code"]) >= 16 for t in tables)
    revisions = [
        json.loads(b)["revision"] for _, p, b in worker.calls if p == "/integration/tables"
    ]
    assert revisions and all(isinstance(r, int) and r > 0 for r in revisions)


async def test_second_publish_skips_media_already_pushed_and_version_increases(
    client: httpx.AsyncClient, db_session: AsyncSession, worker: FakeWorker
) -> None:
    _, mgr = await _seed(db_session)
    await _menu(client, mgr)
    first = (await client.post("/api/v1/online-order/publish", headers=_auth(mgr))).json()
    worker.calls.clear()
    second = (await client.post("/api/v1/online-order/publish", headers=_auth(mgr))).json()
    assert second["version"] > first["version"]
    assert worker.paths() == ["/integration/tables", "/integration/menu"]
    # 桌位碼不會每次發佈都換（印好的 QR 才不會失效）
    assert worker.tables() and {t["code"] for t in worker.tables()} == {
        t["code"]
        for t in (await client.get("/api/v1/online-order/status", headers=_auth(mgr))).json()[
            "tables"
        ]
    }


async def test_rotate_table_code_invalidates_old_one_and_pushes_immediately(
    client: httpx.AsyncClient, db_session: AsyncSession, worker: FakeWorker
) -> None:
    _, mgr = await _seed(db_session)
    await _menu(client, mgr)
    await client.post("/api/v1/online-order/publish", headers=_auth(mgr))
    before = {t["label"]: t["code"] for t in worker.tables()}
    worker.calls.clear()
    resp = await client.post("/api/v1/online-order/tables/A1/rotate", headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    assert worker.paths() == ["/integration/tables"]
    after = {t["label"]: t["code"] for t in worker.tables()}
    assert after["A1"] != before["A1"]
    assert after["A2"] == before["A2"]
    logs = (
        await db_session.scalars(
            select(AuditLog).where(AuditLog.action == "ROTATE_ONLINE_TABLE_CODE")
        )
    ).all()
    assert len(logs) == 1
    assert before["A1"] not in json.dumps(logs[0].before)  # 稽核不記碼本身


async def test_status_lists_table_urls(
    client: httpx.AsyncClient, db_session: AsyncSession, worker: FakeWorker
) -> None:
    _, mgr = await _seed(db_session)
    await _menu(client, mgr)
    await client.post("/api/v1/online-order/publish", headers=_auth(mgr))
    status = (await client.get("/api/v1/online-order/status", headers=_auth(mgr))).json()
    assert status["configured"] is True
    assert status["last_version"] is not None
    urls = {t["label"]: t["url"] for t in status["tables"]}
    code = {t["label"]: t["code"] for t in worker.tables()}["A1"]
    assert urls["A1"] == f"{BASE}/t/{code}"


async def test_remote_failure_reports_error_and_records_nothing(
    client: httpx.AsyncClient, db_session: AsyncSession, worker: FakeWorker
) -> None:
    _, mgr = await _seed(db_session)
    await _menu(client, mgr)
    worker.fail_on = "/integration/menu"
    resp = await client.post("/api/v1/online-order/publish", headers=_auth(mgr))
    assert resp.status_code == 502
    assert "線上點餐" in resp.json()["detail"]
    assert await db_session.scalar(select(func.count()).select_from(OnlineMenuPublication)) == 0
    # 重試：照片和字型雲端已有（204），菜單這次成功
    worker.fail_on = None
    retry = await client.post("/api/v1/online-order/publish", headers=_auth(mgr))
    assert retry.status_code == 200, retry.text


async def test_clerk_cannot_publish_or_rotate(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, _ = await _seed(db_session)
    assert (
        await client.post("/api/v1/online-order/publish", headers=_auth(clerk))
    ).status_code == 403
    assert (
        await client.post("/api/v1/online-order/tables/A1/rotate", headers=_auth(clerk))
    ).status_code == 403


async def test_not_configured_is_a_clear_409(db_session: AsyncSession) -> None:
    _, mgr = await _seed(db_session)
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[get_online_order_client] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        # 查狀態放前面：失敗的發佈會回滾整個測試交易（docs/50 §8）。
        status_resp = await c.get("/api/v1/online-order/status", headers=_auth(mgr))
        assert status_resp.status_code == 200, status_resp.text
        status = status_resp.json()
        resp = await c.post("/api/v1/online-order/publish", headers=_auth(mgr))
    assert resp.status_code == 409
    assert "尚未設定" in resp.json()["detail"]
    assert status["configured"] is False


async def test_rotate_unknown_table_is_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    resp = await client.post("/api/v1/online-order/tables/Z9/rotate", headers=_auth(mgr))
    assert resp.status_code == 404
    assert "Z9" in resp.json()["detail"]


async def _outsider_call(
    db_session: AsyncSession, worker: FakeWorker, method: str, path: str
) -> tuple[httpx.Response, httpx.Response]:
    """別家店的店長打這組雲端（雲端綁定的是「露坑」）。回 (狀態, 要測的請求)；失敗的請求會回滾
    整個測試交易，所以只能放最後一步（docs/50 §8）。"""
    await _seed(db_session)
    own_store_id = _STORE["id"]
    other = Store(name="別家店")
    db_session.add(other)
    await db_session.flush()
    outsider = User(store_id=other.id, username="mgr2", password_hash="h", role=UserRole.MANAGER)
    db_session.add(outsider)
    await db_session.flush()
    token = encode_access_token(user_id=outsider.id, role="MANAGER", store_id=other.id)
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[get_online_order_client] = lambda: OnlineOrderClient(
        BASE, SECRET, store_id=own_store_id, transport=httpx.MockTransport(worker.handler)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        status = await c.get("/api/v1/online-order/status", headers=_auth(token))
        resp = await c.request(method, path, headers=_auth(token))
    return status, resp


async def test_other_store_cannot_publish_to_this_deployment(
    db_session: AsyncSession, worker: FakeWorker
) -> None:
    """Codex 對抗審查 O3：雲端只服務一家店；別家店的店長不能蓋掉它的菜單。"""
    status, resp = await _outsider_call(db_session, worker, "POST", "/api/v1/online-order/publish")
    assert status.json()["configured"] is False
    assert resp.status_code == 409
    assert worker.calls == []


async def test_other_store_cannot_rotate_this_deployments_tables(
    db_session: AsyncSession, worker: FakeWorker
) -> None:
    _, resp = await _outsider_call(
        db_session, worker, "POST", "/api/v1/online-order/tables/A1/rotate"
    )
    assert resp.status_code == 409
    assert worker.calls == []


async def test_rotation_survives_lost_response(
    client: httpx.AsyncClient, db_session: AsyncSession, worker: FakeWorker
) -> None:
    """Codex 對抗審查 O3：雲端已換碼但回應遺失時，本機也要記住新碼；
    下次發佈不能把被停用的舊碼推回去（否則被拍走的 QR 又能用）。"""
    _, mgr = await _seed(db_session)
    await _menu(client, mgr)
    await client.post("/api/v1/online-order/publish", headers=_auth(mgr))
    old = {t["label"]: t["code"] for t in worker.tables()}["A1"]
    worker.fail_on = "/integration/tables"  # 雲端出錯／回應遺失
    resp = await client.post("/api/v1/online-order/tables/A1/rotate", headers=_auth(mgr))
    assert resp.status_code == 502
    assert "再按一次" in resp.json()["detail"]
    status = (await client.get("/api/v1/online-order/status", headers=_auth(mgr))).json()
    local = {t["label"]: t["code"] for t in status["tables"]}["A1"]
    assert local != old  # 本機已記住新碼
    worker.fail_on = None
    await client.post("/api/v1/online-order/publish", headers=_auth(mgr))
    pushed = {t["label"]: t["code"] for t in worker.tables()}["A1"]
    assert pushed == local
    assert pushed != old
