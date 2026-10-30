"""寄售帳篷清單推到雲端（docs/97 §4.3）。

用假的 Worker 接住店內送出的請求（每個都驗簽）：內容沒變就不推；雲端說缺照片就補傳再推；
雲端出錯記下原因、下次重試；推之前清掉已售出的保留勾選；沒設定雲端時什麼都不做。
"""

import asyncio
import hashlib
import hmac
import io
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.inventory.models import SerializedItem
from app.modules.onlineorder import scheduler, tent_publish_service
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.models import OnlineTentListing
from app.modules.onlineorder.router import get_online_order_client
from app.modules.onlineorder.scheduler import publish_tents_once
from app.modules.onlineorder.signing import canonical_string
from app.modules.onlineorder.tent_publish_service import TentPublishService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, SerializedItemStatus, UserRole

SECRET = "tent-publish-secret"
CLOUD = "https://order.test"
API = "/api/v1/online-order/tents"


@dataclass
class FakeWorker:
    """驗簽、記下請求；照片要先傳才收清單（同 online-order/src/tents.ts）。"""

    calls: list[tuple[str, str]] = field(default_factory=list)
    photos: set[str] = field(default_factory=set)
    feeds: list[dict[str, Any]] = field(default_factory=list)
    fail_status: int | None = None
    fail_error: str = "boom"
    # 收下清單後故意讓回應遺失（模擬雲端已存、POS 沒收到）
    drop_response: bool = False
    # 端點用的雲端綁哪家店（seed 之後才知道 id）
    store_id: int = 0

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
        self.calls.append((request.method, path))
        if self.fail_status is not None:
            return httpx.Response(self.fail_status, json={"error": self.fail_error})
        if path.startswith("/integration/tent-photos/"):
            sha = path.rsplit("/", 1)[1]
            assert hashlib.sha256(body).hexdigest() == sha
            self.photos.add(sha)
            return httpx.Response(201)
        if path == "/integration/tents":
            feed = json.loads(body)
            missing = sorted({h for t in feed["tents"] for h in t["photos"]} - self.photos)
            if missing:
                return httpx.Response(409, json={"error": "missing_photo", "missing": missing})
            self.feeds.append(feed)
            if self.drop_response:
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(200, json={"version": feed["version"]})
        return httpx.Response(404, json={"error": "not_found"})

    def tent_puts(self) -> int:
        return sum(1 for _, p in self.calls if p == "/integration/tents")


@dataclass
class Ctx:
    store_id: int
    clerk: str
    item: SerializedItem


async def seed(session: AsyncSession) -> Ctx:
    store = Store(name="露坑")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="露坑-clerk", password_hash="h", role=UserRole.CLERK)
    consignor = Contact(store_id=store.id, name="寄售人")
    session.add_all([clerk, consignor])
    await session.flush()
    item = SerializedItem(
        store_id=store.id,
        item_code="T-1",
        name="Amenity Dome M",
        grade=Grade.A,
        ownership_type=OwnershipType.CONSIGNMENT,
        consignor_id=consignor.id,
        commission_pct=30,
        listed_price=Decimal(6800),
    )
    session.add(item)
    await session.commit()
    return Ctx(
        store.id, encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id), item
    )


def _client(worker: FakeWorker, store_id: int) -> OnlineOrderClient:
    return OnlineOrderClient(
        CLOUD, SECRET, store_id=store_id, transport=httpx.MockTransport(worker.handler)
    )


@pytest_asyncio.fixture
async def api(db_session: AsyncSession) -> AsyncGenerator[tuple[httpx.AsyncClient, FakeWorker]]:
    worker = FakeWorker()
    app = create_app()

    async def session_override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_online_order_client] = lambda: _client(worker, worker.store_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, worker


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (180, 120, 80)).save(buf, format="JPEG")
    return buf.getvalue()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _list_it(c: httpx.AsyncClient, ctx: Ctx, **extra: object) -> None:
    body = {"published": True, "color": "象牙白", **extra}
    resp = await c.put(f"{API}/{ctx.item.id}", json=body, headers=_auth(ctx.clerk))
    assert resp.status_code == 200, resp.text


async def test_publish_uploads_missing_photos_then_skips_when_unchanged(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    c, worker = api
    ctx = await seed(db_session)
    await _list_it(c, ctx)
    up = await c.post(
        f"{API}/{ctx.item.id}/photos",
        files={"file": ("p.jpg", _jpeg(), "image/jpeg")},
        headers=_auth(ctx.clerk),
    )
    sha = up.json()["photos"][0]
    svc = TentPublishService(db_session, _client(worker, ctx.store_id))
    now = datetime(2026, 10, 30, 6, 20, tzinfo=UTC)

    first = await svc.publish(ctx.store_id, now=now)
    assert first.pushed is True
    assert worker.photos == {sha}
    assert worker.tent_puts() == 2  # 第一次被說缺照片，補傳後再送
    [feed] = worker.feeds
    assert feed["syncedAt"] == "2026-10-30T14:20:00+08:00"
    assert feed["soldDays"] == 30
    assert [t["id"] for t in feed["tents"]] == ["T-1"]

    again = await svc.publish(ctx.store_id, now=now + timedelta(minutes=1))
    assert again.pushed is False
    assert worker.tent_puts() == 2


async def test_changes_are_pushed_with_a_newer_version(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    c, worker = api
    ctx = await seed(db_session)
    await _list_it(c, ctx)
    svc = TentPublishService(db_session, _client(worker, ctx.store_id))
    now = datetime.now(UTC)
    await svc.publish(ctx.store_id, now=now)
    await _list_it(c, ctx, reserved=True)
    # 時鐘倒退也一樣往前推版本
    await svc.publish(ctx.store_id, now=now - timedelta(hours=1))
    assert len(worker.feeds) == 2
    assert worker.feeds[1]["version"] > worker.feeds[0]["version"]
    assert worker.feeds[1]["tents"][0]["status"] == "reserved"


async def test_cloud_error_is_recorded_and_retried_next_time(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    c, worker = api
    ctx = await seed(db_session)
    await _list_it(c, ctx)
    svc = TentPublishService(db_session, _client(worker, ctx.store_id))
    worker.fail_status = 500
    failed = await svc.publish(ctx.store_id, now=datetime.now(UTC))
    assert failed.pushed is False
    assert failed.last_error is not None and "500" in failed.last_error
    worker.fail_status = None
    ok = await svc.publish(ctx.store_id, now=datetime.now(UTC))
    assert ok.pushed is True
    assert ok.last_error is None


async def test_unreachable_cloud_is_recorded(db_session: AsyncSession) -> None:
    ctx = await seed(db_session)
    db_session.add(
        OnlineTentListing(store_id=ctx.store_id, serialized_item_id=ctx.item.id, published=True)
    )
    await db_session.flush()

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = OnlineOrderClient(
        CLOUD, SECRET, store_id=ctx.store_id, transport=httpx.MockTransport(offline)
    )
    result = await TentPublishService(db_session, client).publish(
        ctx.store_id, now=datetime.now(UTC)
    )
    assert result.pushed is False
    assert result.last_error is not None


async def test_publish_clears_reserved_mark_of_sold_tents(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    c, worker = api
    ctx = await seed(db_session)
    await _list_it(c, ctx, reserved=True)
    ctx.item.status, ctx.item.sold_date = SerializedItemStatus.SOLD, datetime.now(UTC)
    await db_session.flush()
    await TentPublishService(db_session, _client(worker, ctx.store_id)).publish(
        ctx.store_id, now=datetime.now(UTC)
    )
    row = await db_session.scalar(
        select(OnlineTentListing).where(OnlineTentListing.serialized_item_id == ctx.item.id)
    )
    assert row is not None and row.reserved is False
    assert worker.feeds[0]["tents"][0]["status"] == "sold"


async def test_not_configured_or_other_store_does_nothing(db_session: AsyncSession) -> None:
    ctx = await seed(db_session)
    none = await TentPublishService(db_session, None).publish(ctx.store_id, now=datetime.now(UTC))
    assert (none.configured, none.pushed) == (False, False)
    worker = FakeWorker()
    other = await TentPublishService(db_session, _client(worker, ctx.store_id + 999)).publish(
        ctx.store_id, now=datetime.now(UTC)
    )
    assert other.configured is False
    assert worker.calls == []


async def test_status_and_publish_endpoints(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    c, worker = api
    ctx = await seed(db_session)
    worker.store_id = ctx.store_id
    await _list_it(c, ctx)
    before = await c.get(f"{API}/status", headers=_auth(ctx.clerk))
    assert before.status_code == 200, before.text
    assert before.json() == {
        "configured": True,
        "pending": True,
        "pushed_at": None,
        "last_error": None,
        "last_attempt_at": None,
    }
    pushed = await c.post(f"{API}/publish", headers=_auth(ctx.clerk))
    assert pushed.status_code == 200, pushed.text
    body = pushed.json()
    assert body["pending"] is False
    assert body["pushed_at"] is not None
    assert len(worker.feeds) == 1


async def test_scheduler_publish_is_a_no_op_without_cloud_settings() -> None:
    """沒設定雲端網址／密鑰：背景工作不推帳篷清單。"""
    await publish_tents_once()


async def test_lost_response_does_not_suppress_the_corrective_push(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    """雲端其實收下了、回應卻遺失；店員把內容改回上次成功的樣子，仍要再推（Codex 對抗審查）。"""
    c, worker = api
    ctx = await seed(db_session)
    svc = TentPublishService(db_session, _client(worker, ctx.store_id))
    now = datetime.now(UTC)
    assert (await svc.publish(ctx.store_id, now=now)).pushed is True  # 空清單
    await _list_it(c, ctx)
    worker.drop_response = True
    lost = await svc.publish(ctx.store_id, now=now + timedelta(seconds=1))
    assert lost.pushed is False
    assert worker.feeds[-1]["tents"] != []  # 雲端停在有這頂的版本
    worker.drop_response = False
    worker.store_id = ctx.store_id
    pending = await c.get(f"{API}/status", headers=_auth(ctx.clerk))
    assert pending.json()["pending"] is True
    # 改回空清單：和上次「成功」的內容一樣，但上次失敗過，一定要推
    await _list_it(c, ctx, published=False)
    again = await svc.publish(ctx.store_id, now=now + timedelta(seconds=2))
    assert again.pushed is True
    assert worker.feeds[-1]["tents"] == []


async def test_unchanged_feed_is_reconfirmed_every_30_minutes(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    """內容沒變也定期重送，雲端藉此確認照片都在（不在就回缺照片、補傳）。"""
    c, worker = api
    ctx = await seed(db_session)
    await _list_it(c, ctx)
    svc = TentPublishService(db_session, _client(worker, ctx.store_id))
    now = datetime.now(UTC)
    await svc.publish(ctx.store_id, now=now)
    assert (await svc.publish(ctx.store_id, now=now + timedelta(minutes=29))).pushed is False
    assert (await svc.publish(ctx.store_id, now=now + timedelta(minutes=31))).pushed is True
    assert len(worker.feeds) == 2


async def test_sold_tent_feed_drops_video_too(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    c, worker = api
    ctx = await seed(db_session)
    await _list_it(c, ctx, video_url="https://youtu.be/cSArnUe7cEY")
    ctx.item.status, ctx.item.sold_date = SerializedItemStatus.SOLD, datetime.now(UTC)
    await db_session.flush()
    await TentPublishService(db_session, _client(worker, ctx.store_id)).publish(
        ctx.store_id, now=datetime.now(UTC)
    )
    sold = worker.feeds[-1]["tents"][0]
    assert (sold["status"], sold["photos"], sold["video"]) == ("sold", [], None)


async def test_slow_tent_publish_never_delays_order_pulling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """推帳篷卡住（照片上傳很慢）時，拉單／心跳照常每輪跑（雲端 2 分鐘沒心跳會暫停線上接單）。"""
    ticks = 0
    publishes = 0
    release = asyncio.Event()

    async def tick() -> None:
        nonlocal ticks
        ticks += 1

    async def slow_publish() -> None:
        nonlocal publishes
        publishes += 1
        await release.wait()

    monkeypatch.setattr(scheduler, "tick_once", tick)
    monkeypatch.setattr(scheduler, "publish_tents_once", slow_publish)
    monkeypatch.setattr(scheduler, "PULL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(scheduler, "TENT_PUBLISH_INTERVAL_SECONDS", 0)
    stop = asyncio.Event()
    loop = asyncio.create_task(scheduler.scheduler_loop(stop))
    for _ in range(200):
        if ticks >= 5:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(loop, timeout=2)
    assert ticks >= 5
    # 上一輪還沒推完就不會再開新的一輪
    assert publishes == 1


async def test_publish_while_another_is_running_returns_at_once(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    """已有一份在推：不排隊（排隊會拿著資料庫連線等），直接回、交給下一輪補（Codex 第二輪）。"""
    c, worker = api
    ctx = await seed(db_session)
    await _list_it(c, ctx)
    svc = TentPublishService(db_session, _client(worker, ctx.store_id))
    async with tent_publish_service._PUBLISH_LOCK:
        busy = await asyncio.wait_for(svc.publish(ctx.store_id, now=datetime.now(UTC)), timeout=2)
    assert (busy.configured, busy.pushed) == (True, False)
    assert worker.calls == []
    # 鎖放開後照常推
    assert (await svc.publish(ctx.store_id, now=datetime.now(UTC))).pushed is True


async def test_change_during_a_running_push_goes_out_right_after_it(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    """推送進行中又有人按「立即更新」：不排隊，但進行中那份推完會馬上再推一輪最新內容。"""
    c, worker = api
    ctx = await seed(db_session)
    await _list_it(c, ctx)
    svc = TentPublishService(db_session, _client(worker, ctx.store_id))
    row = await db_session.scalar(
        select(OnlineTentListing).where(OnlineTentListing.serialized_item_id == ctx.item.id)
    )
    assert row is not None
    seen: list[bool] = []
    real = worker.handler

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/integration/tents" and not seen:
            # 第一份還在推的時候：店員又改了資料、按了立即更新
            row.reserved = True
            await db_session.flush()
            busy = await svc.publish(ctx.store_id, now=datetime.now(UTC))
            seen.append(busy.pushed)
        return real(request)

    client = OnlineOrderClient(
        CLOUD, SECRET, store_id=ctx.store_id, transport=httpx.MockTransport(handler)
    )
    await TentPublishService(db_session, client).publish(ctx.store_id, now=datetime.now(UTC))
    assert seen == [False]  # 第二個請求沒有等
    assert [t["status"] for f in worker.feeds for t in f["tents"]] == ["available", "reserved"]


async def test_cloud_busy_is_explained_and_retried(
    api: tuple[httpx.AsyncClient, FakeWorker], db_session: AsyncSession
) -> None:
    """雲端同店正在處理另一份（鎖住）：記下看得懂的原因，下一輪再推。"""
    c, worker = api
    ctx = await seed(db_session)
    await _list_it(c, ctx)
    svc = TentPublishService(db_session, _client(worker, ctx.store_id))
    worker.fail_status, worker.fail_error = 409, "busy"
    busy = await svc.publish(ctx.store_id, now=datetime.now(UTC))
    assert busy.pushed is False
    assert busy.last_error is not None and "正在處理" in busy.last_error
    worker.fail_status = None
    assert (await svc.publish(ctx.store_id, now=datetime.now(UTC))).pushed is True
