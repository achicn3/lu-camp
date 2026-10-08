"""線上訂單店內端（docs/44 §3.7、§4.3、§4.5；O4b）。

POS 每幾秒向雲端拉新單（假 Worker 接住）：
- 匯入只做一次（重拉同一張不重複，C3）；回報雲端排進持久化佇列（C4），失敗退避重試、
  雲端明確拒收不再重試。
- 有限量品項的單在同一交易內直接扣每日限量份數＝保留，回報 HELD；不夠就 REJECTED（哪一項不夠）。
- 現金單 30 分鐘沒來付：保留到期加回份數，單子不取消。
- 取消：保留加回、回報 VOIDED＋CANCELLED。
- 帶入結帳：以 POS 目前的菜單重新計價（價格變了要看得出差額）；結帳成立銷售時先加回保留
  再照一般結帳扣
  （淨額只扣一次），回報 SETTLED＋PAID；同一張線上單只能成立一筆銷售。
"""

import hashlib
import hmac
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.core.time import utc_now
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.customerdisplay.schemas import CartUpsertRequest, StaffCartPayloadRead
from app.modules.customerdisplay.service import CartSessionConflict, CustomerDisplayService
from app.modules.menu.models import MenuItem
from app.modules.menu.service import MenuService
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.experience_service import MenuExperienceService
from app.modules.onlineorder.models import (
    OnlineOrder,
    OnlineOrderOutbox,
    StockReservation,
)
from app.modules.onlineorder.orders_service import OnlineOrdersService
from app.modules.onlineorder.presentation_schemas import MenuExperienceWriteRequest
from app.modules.onlineorder.router import get_online_order_client
from app.modules.onlineorder.scheduler import tick_once
from app.modules.onlineorder.signing import canonical_string
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import SaleLineType, ServiceMode, TenderType, UserRole
from app.shared.exceptions import OnlineOrderNotConfigured, SignatureContentMismatch
from tests.integration.customer_display_helpers import (
    CustomerDisplayAwareClient,
    ensure_paired_customer_display,
)

SECRET = "orders-test-secret"
BASE = "https://order.test"


@dataclass
class FakeWorker:
    """雲端：拉單回 `orders`；回報記下來。`status_reply` 可指定回報的回應（模擬失敗）。"""

    orders: list[Any] = field(default_factory=list)  # 也放壞資料，測匯入會跳過
    reports: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    status_reply: tuple[int, dict[str, Any]] | None = None
    store_status: list[bool] = field(default_factory=list)
    accepting: bool = True
    paused_reason: str | None = None
    pull_fails: bool = False

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
        if request.method == "GET" and path == "/integration/orders":
            if self.pull_fails:
                return httpx.Response(503, json={"error": "down"})
            return httpx.Response(
                200,
                json={
                    "accepting": self.accepting,
                    "paused_reason": self.paused_reason,
                    "server_time": "2026-10-02T08:00:00.000Z",
                    "orders": self.orders,
                },
            )
        if request.method == "POST" and path.startswith("/integration/orders/"):
            remote_id = path.split("/")[3]
            payload = json.loads(body)
            self.reports.append((remote_id, payload))
            if self.status_reply is not None:
                code, reply = self.status_reply
                return httpx.Response(code, json=reply)
            return httpx.Response(200, json={"id": remote_id, **payload})
        if request.method == "PUT" and path == "/integration/store-status":
            accepting = bool(json.loads(body)["accepting"])
            self.store_status.append(accepting)
            self.accepting = accepting
            return httpx.Response(200, json={"accepting": accepting, "paused_reason": None})
        return httpx.Response(404, json={"error": "not_found"})


@dataclass
class Ctx:
    store_id: int = 0
    clerk_id: int = 0
    clerk: str = ""
    cake: int = 0  # 每日限量
    latte: int = 0  # 不限量
    worker: FakeWorker = field(default_factory=FakeWorker)


_CTX: dict[str, Ctx] = {}


def _client(worker: FakeWorker, store_id: int) -> OnlineOrderClient:
    return OnlineOrderClient(
        BASE, SECRET, store_id=store_id, transport=httpx.MockTransport(worker.handler)
    )


@pytest_asyncio.fixture
async def ctx(db_session: AsyncSession) -> Ctx:
    store = Store(name="露坑")
    db_session.add(store)
    await db_session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    db_session.add_all([clerk, mgr])
    await db_session.flush()
    await CashDrawerService(db_session).open_session(store.id, clerk.id, Decimal("1000"))
    menu = MenuService(db_session)
    cake = await menu.create_menu_item(
        store.id, name="戚風", unit_price=Decimal(90), actor_user_id=mgr.id
    )
    await menu.update_menu_item(store.id, cake.id, daily_limited=True, actor_user_id=mgr.id)
    await menu.set_daily_stock(
        store.id, "item", cake.id, qty=3, expected_remaining=0, actor_user_id=mgr.id
    )
    latte = await menu.create_menu_item(
        store.id, name="拿鐵", unit_price=Decimal(150), actor_user_id=mgr.id
    )
    c = Ctx(
        store_id=store.id,
        clerk_id=clerk.id,
        clerk=encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        cake=cake.id,
        latte=latte.id,
    )
    _CTX["c"] = c
    return c


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, ctx: Ctx) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[get_online_order_client] = lambda: _client(ctx.worker, ctx.store_id)
    transport = httpx.ASGITransport(app=app)
    async with CustomerDisplayAwareClient(
        transport=transport, base_url="http://test", db_session=db_session
    ) as c:
        yield c
    app.dependency_overrides.clear()


def _order(
    remote_id: str, lines: list[dict[str, Any]], *, hold: str = "NONE", table: str = "A1"
) -> dict[str, Any]:
    total = sum(int(line["line_total"]) for line in lines)
    return {
        "id": remote_id,
        "table_label": table,
        "service_mode": "DINE_IN",
        "menu_version": 1,
        "total": total,
        "payment_method": "CASH",
        "payment_status": "UNPAID",
        "hold_status": hold,
        "note": "少冰",
        "created_at": "2026-10-02T07:59:00.000Z",
        "lines": lines,
    }


def _line(
    no: int, item_id: int, name: str, price: int, qty: int = 1, limited: bool = False
) -> dict[str, Any]:
    return {
        "line_no": no,
        "item_id": item_id,
        "name": name,
        "option_ids": [],
        "unit_price": price,
        "qty": qty,
        "line_total": price * qty,
        "limited": limited,
    }


def _svc(session: AsyncSession, c: Ctx) -> OnlineOrdersService:
    return OnlineOrdersService(session, _client(c.worker, c.store_id))


async def _cake_left(session: AsyncSession, c: Ctx) -> int | None:
    item = await session.get(MenuItem, c.cake)
    assert item is not None
    await session.refresh(item)
    return item.stock_qty


async def _order_row(session: AsyncSession, remote_id: str) -> OnlineOrder:
    row = await session.scalar(select(OnlineOrder).where(OnlineOrder.remote_id == remote_id))
    assert row is not None
    await session.refresh(row)
    return row


def _rid(n: int) -> str:
    return f"{n:032x}"


def _h(token: str, idem: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        h["Idempotency-Key"] = idem
    return h


# ── 拉單與匯入 ────────────────────────────────────────────────────────


async def test_pull_imports_once_and_reports_imported(db_session: AsyncSession, ctx: Ctx) -> None:
    ctx.worker.orders = [_order(_rid(1), [_line(1, ctx.latte, "拿鐵", 150)])]
    svc = _svc(db_session, ctx)
    result = await svc.pull_once(ctx.store_id)
    assert result.imported == 1
    await svc.flush_outbox(ctx.store_id)
    assert ctx.worker.reports == [(_rid(1), {"sync_status": "IMPORTED"})]
    # 雲端還沒收到回報前又被拉到：不會匯入第二次
    again = await svc.pull_once(ctx.store_id)
    assert again.imported == 0
    rows = (await db_session.scalars(select(OnlineOrder))).all()
    assert len(rows) == 1
    row = rows[0]
    assert (row.sync_status, row.hold_status, row.payment_status) == ("IMPORTED", "NONE", "UNPAID")
    assert (row.table_label, row.total, row.note) == ("A1", Decimal(150), "少冰")


async def test_limited_line_is_reserved_by_consuming_daily_stock(
    db_session: AsyncSession, ctx: Ctx
) -> None:
    ctx.worker.orders = [
        _order(
            _rid(2), [_line(1, ctx.cake, "戚風", 90, qty=2, limited=True)], hold="HOLD_REQUESTED"
        )
    ]
    svc = _svc(db_session, ctx)
    await svc.pull_once(ctx.store_id)
    await svc.flush_outbox(ctx.store_id)
    assert await _cake_left(db_session, ctx) == 1  # 3 份扣掉 2 份
    row = await _order_row(db_session, _rid(2))
    assert row.hold_status == "HELD"
    reservation = await db_session.scalar(
        select(StockReservation).where(StockReservation.online_order_id == row.id)
    )
    assert reservation is not None and reservation.status == "ACTIVE"
    assert ctx.worker.reports == [(_rid(2), {"sync_status": "IMPORTED", "hold_status": "HELD"})]


async def test_not_enough_stock_rejects_without_touching_stock(
    db_session: AsyncSession, ctx: Ctx
) -> None:
    ctx.worker.orders = [
        _order(
            _rid(3),
            [_line(1, ctx.latte, "拿鐵", 150), _line(2, ctx.cake, "戚風", 90, qty=5, limited=True)],
            hold="HOLD_REQUESTED",
        )
    ]
    svc = _svc(db_session, ctx)
    await svc.pull_once(ctx.store_id)
    await svc.flush_outbox(ctx.store_id)
    assert await _cake_left(db_session, ctx) == 3
    row = await _order_row(db_session, _rid(3))
    assert row.hold_status == "REJECTED"
    assert row.reject_reason is not None and "戚風" in row.reject_reason
    assert ctx.worker.reports == [(_rid(3), {"sync_status": "IMPORTED", "hold_status": "REJECTED"})]


async def test_reservation_expires_after_30_minutes_but_order_stays(
    db_session: AsyncSession, ctx: Ctx
) -> None:
    ctx.worker.orders = [
        _order(_rid(4), [_line(1, ctx.cake, "戚風", 90, limited=True)], hold="HOLD_REQUESTED")
    ]
    svc = _svc(db_session, ctx)
    await svc.pull_once(ctx.store_id)
    assert await _cake_left(db_session, ctx) == 2
    expired = await svc.expire_reservations(ctx.store_id, now=utc_now() + timedelta(minutes=31))
    assert expired == 1
    assert await _cake_left(db_session, ctx) == 3
    row = await _order_row(db_session, _rid(4))
    assert (row.sync_status, row.payment_status) == ("IMPORTED", "UNPAID")
    assert row.hold_status == "NONE"
    await svc.flush_outbox(ctx.store_id)
    await svc.flush_outbox(ctx.store_id)
    assert ctx.worker.reports[-1] == (_rid(4), {"hold_status": "NONE"})
    assert await svc.expire_reservations(ctx.store_id, now=utc_now() + timedelta(minutes=32)) == 0


# ── 回報佇列 ──────────────────────────────────────────────────────────


async def test_outbox_retries_on_failure_and_gives_up_on_explicit_rejection(
    db_session: AsyncSession, ctx: Ctx
) -> None:
    ctx.worker.orders = [_order(_rid(5), [_line(1, ctx.latte, "拿鐵", 150)])]
    svc = _svc(db_session, ctx)
    await svc.pull_once(ctx.store_id)
    ctx.worker.status_reply = (503, {"error": "down"})
    await svc.flush_outbox(ctx.store_id)
    entry = await db_session.scalar(select(OnlineOrderOutbox))
    assert entry is not None
    await db_session.refresh(entry)
    assert (entry.status, entry.attempts) == ("PENDING", 1)
    assert entry.next_attempt_at > utc_now()
    # 還沒到下次重試時間：不送
    sent_before = len(ctx.worker.reports)
    await svc.flush_outbox(ctx.store_id)
    assert len(ctx.worker.reports) == sent_before
    # 到了時間、雲端明確拒收 → 不再重試
    ctx.worker.status_reply = (409, {"error": "invalid_transition"})
    await svc.flush_outbox(ctx.store_id, now=utc_now() + timedelta(minutes=10))
    await db_session.refresh(entry)
    assert entry.status == "DEAD" and entry.last_error is not None


# ── 取消、帶入結帳 ───────────────────────────────────────────────────


async def _pulled(
    db_session: AsyncSession,
    ctx: Ctx,
    remote_id: str,
    lines: list[dict[str, Any]],
    hold: str = "NONE",
) -> OnlineOrder:
    ctx.worker.orders = [_order(remote_id, lines, hold=hold)]
    svc = _svc(db_session, ctx)
    await svc.pull_once(ctx.store_id)
    await svc.flush_outbox(ctx.store_id)
    ctx.worker.reports.clear()
    ctx.worker.orders = []
    return await _order_row(db_session, remote_id)


async def test_list_and_cancel_releases_reservation(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    row = await _pulled(
        db_session, ctx, _rid(6), [_line(1, ctx.cake, "戚風", 90, limited=True)], "HOLD_REQUESTED"
    )
    listed = await client.get("/api/v1/online-orders", headers=_h(ctx.clerk))
    assert listed.status_code == 200, listed.text
    assert [o["id"] for o in listed.json()["orders"]] == [row.id]
    resp = await client.post(f"/api/v1/online-orders/{row.id}/cancel", headers=_h(ctx.clerk))
    assert resp.status_code == 200, resp.text
    assert resp.json()["sync_status"] == "VOIDED"
    assert await _cake_left(db_session, ctx) == 3
    await _svc(db_session, ctx).flush_outbox(ctx.store_id)
    assert ctx.worker.reports == [
        (_rid(6), {"sync_status": "VOIDED", "payment_status": "CANCELLED"})
    ]


async def test_cart_reprices_with_current_menu(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    row = await _pulled(db_session, ctx, _rid(7), [_line(1, ctx.latte, "拿鐵", 150, qty=2)])
    latte = await db_session.get(MenuItem, ctx.latte)
    assert latte is not None
    latte.unit_price = Decimal(160)  # 客人送單後改價
    await db_session.flush()
    resp = await client.get(f"/api/v1/online-orders/{row.id}/cart", headers=_h(ctx.clerk))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lines"] == [
        {
            "line_no": 1,
            "menu_item_id": ctx.latte,
            "menu_option_ids": [],
            "experience_id": None,
            "qty": 2,
            "description": "拿鐵",
            "online_unit_price": "150",
            "unit_price": "160",
        }
    ]
    assert (body["online_total"], body["total"]) == ("300", "320")
    assert (body["service_mode"], body["table_no"]) == ("DINE_IN", "A1")


async def test_cart_keeps_experience_lines_apart_and_labelled(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    """同品項同選項、一行是體驗卡一行是一般點：帶入 POS 要分得開，體驗那行看得出是體驗。"""
    exp = await MenuExperienceService(db_session).create(
        ctx.store_id,
        MenuExperienceWriteRequest(menu_item_id=ctx.latte, title="拿鐵體驗"),
        actor_user_id=ctx.clerk_id,
    )
    card = {**_line(1, ctx.latte, "拿鐵體驗・拿鐵", 150), "experience_id": exp.id}
    gone = {**_line(3, ctx.latte, "已下架體驗・拿鐵", 150), "experience_id": 999999}
    row = await _pulled(
        db_session, ctx, _rid(40), [card, _line(2, ctx.latte, "拿鐵", 150), gone]
    )
    resp = await client.get(f"/api/v1/online-orders/{row.id}/cart", headers=_h(ctx.clerk))
    assert resp.status_code == 200, resp.text
    lines = resp.json()["lines"]
    assert [(x["line_no"], x["experience_id"], x["description"]) for x in lines] == [
        (1, exp.id, "拿鐵體驗・拿鐵"),
        (2, None, "拿鐵"),
        # 卡片已刪：沿用客人送單時的品名，照樣看得出是體驗
        (3, 999999, "已下架體驗・拿鐵"),
    ]
    listed = await client.get("/api/v1/online-orders", headers=_h(ctx.clerk))
    order = next(o for o in listed.json()["orders"] if o["id"] == row.id)
    assert [x["experience_id"] for x in order["lines"]] == [exp.id, None, 999999]


async def test_checkout_converts_reservation_and_reports_settled(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    row = await _pulled(
        db_session,
        ctx,
        _rid(8),
        [_line(1, ctx.cake, "戚風", 90, qty=2, limited=True)],
        "HOLD_REQUESTED",
    )
    assert await _cake_left(db_session, ctx) == 1
    sale = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "MENU", "menu_item_id": ctx.cake, "qty": 2}],
            "service_mode": "TAKEOUT",
            "online_order_id": row.id,
        },
        headers=_h(ctx.clerk, "online-8"),
    )
    assert sale.status_code == 201, sale.text
    assert await _cake_left(db_session, ctx) == 1  # 保留加回再扣：只扣一次
    row = await _order_row(db_session, _rid(8))
    assert (row.sync_status, row.payment_status, row.sale_id) == (
        "SETTLED",
        "PAID",
        sale.json()["id"],
    )
    reservation = await db_session.scalar(
        select(StockReservation).where(StockReservation.online_order_id == row.id)
    )
    assert reservation is not None and reservation.status == "CONVERTED"
    await _svc(db_session, ctx).flush_outbox(ctx.store_id)
    assert ctx.worker.reports == [(_rid(8), {"sync_status": "SETTLED", "payment_status": "PAID"})]


async def test_quote_counts_the_bound_orders_own_reservation_as_available(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    """線上單保留了最後幾份：帶入 POS 試算時，那張單自己保留的份數算可用（Codex O4 第二輪 high）。

    不帶線上單編號照樣以剩餘份數判斷（別張單、現場客人不能用掉這份保留）；試算不釋放保留。
    """
    row = await _pulled(
        db_session,
        ctx,
        _rid(31),
        [_line(1, ctx.cake, "戚風", 90, qty=3, limited=True)],
        "HOLD_REQUESTED",
    )
    assert await _cake_left(db_session, ctx) == 0  # 三份全被這張線上單保留
    cart = {"lines": [{"line_type": "MENU", "menu_item_id": ctx.cake, "qty": 3}]}

    plain = await client.post("/api/v1/sales/quote", json=cart, headers=_h(ctx.clerk))
    assert plain.status_code == 409, plain.text

    bound = await client.post(
        "/api/v1/sales/quote", json={**cart, "online_order_id": row.id}, headers=_h(ctx.clerk)
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["total"] == "270"
    assert await _cake_left(db_session, ctx) == 0  # 試算不動保留

    over = await client.post(
        "/api/v1/sales/quote",
        json={
            "lines": [{"line_type": "MENU", "menu_item_id": ctx.cake, "qty": 4}],
            "online_order_id": row.id,
        },
        headers=_h(ctx.clerk),
    )
    assert over.status_code == 409, over.text  # 超過保留＋剩餘仍擋

    sale = await client.post(
        "/api/v1/sales",
        json={**cart, "service_mode": "TAKEOUT", "online_order_id": row.id},
        headers=_h(ctx.clerk, "online-31"),
    )
    assert sale.status_code == 201, sale.text
    assert await _cake_left(db_session, ctx) == 0


async def test_online_order_settles_only_once(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    row = await _pulled(db_session, ctx, _rid(9), [_line(1, ctx.latte, "拿鐵", 150)])
    body = {
        "lines": [{"line_type": "MENU", "menu_item_id": ctx.latte, "qty": 1}],
        "service_mode": "TAKEOUT",
        "online_order_id": row.id,
    }
    first = await client.post("/api/v1/sales", json=body, headers=_h(ctx.clerk, "a"))
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/sales", json=body, headers=_h(ctx.clerk, "b"))
    assert second.status_code == 409
    assert "已經結帳" in second.json()["detail"]


async def test_cannot_checkout_cancelled_or_rejected_order(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    row = await _pulled(
        db_session,
        ctx,
        _rid(10),
        [_line(1, ctx.cake, "戚風", 90, qty=9, limited=True)],
        "HOLD_REQUESTED",
    )
    assert row.hold_status == "REJECTED"
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "MENU", "menu_item_id": ctx.latte, "qty": 1}],
            "service_mode": "TAKEOUT",
            "online_order_id": row.id,
        },
        headers=_h(ctx.clerk, "c"),
    )
    assert resp.status_code == 409
    assert "庫存不足" in resp.json()["detail"]


# ── 暫停接單 ─────────────────────────────────────────────────────────


async def test_pause_and_resume_forwarded_to_cloud(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    resp = await client.put(
        "/api/v1/online-orders/accepting", json={"accepting": False}, headers=_h(ctx.clerk)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepting"] is False
    assert ctx.worker.store_status == [False]
    status = await client.get("/api/v1/online-orders", headers=_h(ctx.clerk))
    assert status.json()["accepting"] is False


async def test_pull_failure_is_recorded_for_the_pos(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    ctx.worker.pull_fails = True
    await _svc(db_session, ctx).pull_once(ctx.store_id)
    status = (await client.get("/api/v1/online-orders", headers=_h(ctx.clerk))).json()
    assert status["last_pull_error"]


async def test_scheduler_tick_is_a_no_op_without_cloud_settings() -> None:
    """沒設定雲端網址／密鑰：背景工作什麼都不做（測試與未啟用的門市都是這樣）。"""
    await tick_once()


# ── 邊界 ─────────────────────────────────────────────────────────────


async def test_bad_orders_are_skipped_and_good_ones_still_import(
    db_session: AsyncSession, ctx: Ctx
) -> None:
    good = _order(_rid(20), [_line(1, ctx.latte, "拿鐵", 150)])
    no_lines = {**_order(_rid(21), [_line(1, ctx.latte, "拿鐵", 150)]), "lines": []}
    bad_mode = {**_order(_rid(22), [_line(1, ctx.latte, "拿鐵", 150)]), "service_mode": "BOAT"}
    ctx.worker.orders = ["oops", {"id": "not-hex"}, no_lines, bad_mode, good]
    result = await _svc(db_session, ctx).pull_once(ctx.store_id)
    assert result.imported == 1
    assert [r.remote_id for r in (await db_session.scalars(select(OnlineOrder))).all()] == [
        _rid(20)
    ]


async def test_hold_rejects_item_that_is_archived_or_stopped(
    db_session: AsyncSession, ctx: Ctx
) -> None:
    latte = await db_session.get(MenuItem, ctx.latte)
    assert latte is not None
    latte.is_available = False
    await db_session.flush()
    row = await _pulled(
        db_session, ctx, _rid(23), [_line(1, ctx.latte, "拿鐵", 150)], "HOLD_REQUESTED"
    )
    assert row.hold_status == "REJECTED" and row.reject_reason is not None
    assert "停售" in row.reject_reason
    latte.archived_at = utc_now()
    await db_session.flush()
    gone = await _pulled(
        db_session, ctx, _rid(24), [_line(1, ctx.latte, "拿鐵", 150)], "HOLD_REQUESTED"
    )
    assert gone.reject_reason is not None and "不在菜單上" in gone.reject_reason


async def test_cancel_twice_is_harmless_and_settled_cannot_be_cancelled(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    row = await _pulled(db_session, ctx, _rid(25), [_line(1, ctx.latte, "拿鐵", 150)])
    for _ in range(2):
        resp = await client.post(f"/api/v1/online-orders/{row.id}/cancel", headers=_h(ctx.clerk))
        assert resp.status_code == 200, resp.text
    outbox = (
        await db_session.scalars(
            select(OnlineOrderOutbox).where(OnlineOrderOutbox.online_order_id == row.id)
        )
    ).all()
    assert [o.payload for o in outbox].count(
        {"sync_status": "VOIDED", "payment_status": "CANCELLED"}
    ) == 1
    cart = await client.get(f"/api/v1/online-orders/{row.id}/cart", headers=_h(ctx.clerk))
    assert cart.status_code == 409 and "取消" in cart.json()["detail"]
    settled = await _pulled(db_session, ctx, _rid(26), [_line(1, ctx.latte, "拿鐵", 150)])
    sale = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "MENU", "menu_item_id": ctx.latte, "qty": 1}],
            "service_mode": "TAKEOUT",
            "online_order_id": settled.id,
        },
        headers=_h(ctx.clerk, "s26"),
    )
    assert sale.status_code == 201, sale.text
    cart2 = await client.get(f"/api/v1/online-orders/{settled.id}/cart", headers=_h(ctx.clerk))
    assert cart2.status_code == 409 and "已經結帳" in cart2.json()["detail"]
    resp = await client.post(f"/api/v1/online-orders/{settled.id}/cancel", headers=_h(ctx.clerk))
    assert resp.status_code == 409 and "交易紀錄" in resp.json()["detail"]


async def test_cart_conflicts_when_menu_changed(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx
) -> None:
    row = await _pulled(
        db_session,
        ctx,
        _rid(27),
        [{**_line(1, ctx.latte, "拿鐵", 150), "option_ids": [999999]}],
    )
    resp = await client.get(f"/api/v1/online-orders/{row.id}/cart", headers=_h(ctx.clerk))
    assert resp.status_code == 409 and "菜單選項改過了" in resp.json()["detail"]
    latte = await db_session.get(MenuItem, ctx.latte)
    assert latte is not None
    latte.archived_at = utc_now()
    await db_session.flush()
    gone = await client.get(f"/api/v1/online-orders/{row.id}/cart", headers=_h(ctx.clerk))
    assert gone.status_code == 409 and "不在菜單上" in gone.json()["detail"]
    missing = await client.get("/api/v1/online-orders/999999/cart", headers=_h(ctx.clerk))
    assert missing.status_code == 404


async def test_outbox_keeps_order_and_retries_when_cloud_unreachable(
    db_session: AsyncSession, ctx: Ctx
) -> None:
    ctx.worker.orders = [_order(_rid(28), [_line(1, ctx.latte, "拿鐵", 150)])]

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    svc = _svc(db_session, ctx)
    await svc.pull_once(ctx.store_id)
    offline = OnlineOrdersService(
        db_session,
        OnlineOrderClient(BASE, SECRET, store_id=ctx.store_id, transport=httpx.MockTransport(down)),
    )
    assert await offline.flush_outbox(ctx.store_id) == 0
    entry = await db_session.scalar(select(OnlineOrderOutbox))
    assert entry is not None
    await db_session.refresh(entry)
    assert (entry.status, entry.attempts) == ("PENDING", 1)
    # 網路恢復、過了重試時間：送出
    assert await svc.flush_outbox(ctx.store_id, now=utc_now() + timedelta(minutes=1)) == 1


async def test_service_refuses_without_cloud_or_for_another_store(
    db_session: AsyncSession, ctx: Ctx
) -> None:
    with pytest.raises(OnlineOrderNotConfigured):
        await OnlineOrdersService(db_session, None).pull_once(ctx.store_id)
    with pytest.raises(OnlineOrderNotConfigured):
        await _svc(db_session, ctx).pull_once(ctx.store_id + 1)


# ── 帶入結帳後 POS 重新整理：線上單跟著購物車還原（Codex O4 第一輪 high）──


async def test_cart_keeps_online_order_id_for_restore() -> None:
    payload = CartUpsertRequest.model_validate(
        {
            "lines": [{"line_type": "MENU", "menu_item_id": 1, "qty": 1}],
            "service_mode": "TAKEOUT",
            "online_order_id": 7,
        }
    )
    restored = StaffCartPayloadRead.model_validate(payload.model_dump(mode="json"))
    assert restored.online_order_id == 7
    # 舊購物車沒有這欄 → None
    legacy = payload.model_dump(mode="json")
    del legacy["online_order_id"]
    old = StaffCartPayloadRead.model_validate(legacy)
    assert old.online_order_id is None


async def test_cart_resend_check_compares_online_order(
    db_session: AsyncSession, ctx: Ctx
) -> None:
    """線上單編號不在客顯快照裡：「帶入 → 回應遺失 → 取消帶入」兩次 PUT 快照相同，
    不比對的話第二次會被當成重送而靜默丟掉，重新整理後又變回線上單。"""
    terminal, _device = await ensure_paired_customer_display(
        db_session, store_id=ctx.store_id, actor_user_id=ctx.clerk_id
    )
    display = CustomerDisplayService(db_session)
    lines = [{"line_type": "MENU", "menu_item_id": ctx.latte, "qty": 1}]

    def body(revision: int | None, online: int | None) -> CartUpsertRequest:
        return CartUpsertRequest.model_validate(
            {
                "lines": lines,
                "service_mode": "TAKEOUT",
                **({} if revision is None else {"expected_revision": revision}),
                **({} if online is None else {"online_order_id": online}),
            }
        )

    first = await display.upsert_cart(
        ctx.store_id, terminal.id, body(None, 5), actor_user_id=ctx.clerk_id
    )
    assert first.revision == 1
    second = await display.upsert_cart(
        ctx.store_id, terminal.id, body(1, None), actor_user_id=ctx.clerk_id
    )
    assert second.revision == 2
    with pytest.raises(CartSessionConflict):
        await display.upsert_cart(
            ctx.store_id, terminal.id, body(1, 5), actor_user_id=ctx.clerk_id
        )


async def test_customer_display_cart_counts_the_bound_orders_reservation(
    db_session: AsyncSession, ctx: Ctx
) -> None:
    """顧客螢幕購物車用同一支試算：帶入保留了最後幾份的線上單也要建得起來（Codex O4 第二輪）。"""
    row = await _pulled(
        db_session,
        ctx,
        _rid(32),
        [_line(1, ctx.cake, "戚風", 90, qty=3, limited=True)],
        "HOLD_REQUESTED",
    )
    assert await _cake_left(db_session, ctx) == 0
    terminal, _device = await ensure_paired_customer_display(
        db_session, store_id=ctx.store_id, actor_user_id=ctx.clerk_id
    )
    cart = await CustomerDisplayService(db_session).upsert_cart(
        ctx.store_id,
        terminal.id,
        CartUpsertRequest.model_validate(
            {
                "lines": [{"line_type": "MENU", "menu_item_id": ctx.cake, "qty": 3}],
                "service_mode": "TAKEOUT",
                "online_order_id": row.id,
            }
        ),
        actor_user_id=ctx.clerk_id,
    )
    assert cart.revision == 1
    assert await _cake_left(db_session, ctx) == 0


async def _bound_cart(db_session: AsyncSession, ctx: Ctx, order_id: int) -> tuple[int, int]:
    """配好顧客螢幕、把線上單帶進購物車；回傳 (cart_session_id, revision)。"""
    terminal, _device = await ensure_paired_customer_display(
        db_session, store_id=ctx.store_id, actor_user_id=ctx.clerk_id
    )
    cart = await CustomerDisplayService(db_session).upsert_cart(
        ctx.store_id,
        terminal.id,
        CartUpsertRequest.model_validate(
            {
                "lines": [{"line_type": "MENU", "menu_item_id": ctx.latte, "qty": 1}],
                "service_mode": "TAKEOUT",
                "tenders": [{"tender_type": "CASH", "amount": "150"}],
                "online_order_id": order_id,
            }
        ),
        actor_user_id=ctx.clerk_id,
    )
    return cart.id, cart.revision


@pytest.mark.parametrize("variant", ["omitted", "substituted"])
async def test_checkout_must_match_the_carts_bound_online_order(
    db_session: AsyncSession, ctx: Ctx, variant: str
) -> None:
    """購物車綁了線上單 A：結帳漏帶編號或換成 B 一律擋下（Codex O4 第三輪 high）。

    否則 A 收了錢卻仍是未付款、之後還能再帶入收一次；換成 B 則把錢記到別張單上。
    """
    first = await _pulled(db_session, ctx, _rid(81), [_line(1, ctx.latte, "拿鐵", 150)])
    other = await _pulled(db_session, ctx, _rid(82), [_line(1, ctx.latte, "拿鐵", 150)])
    cart_id, revision = await _bound_cart(db_session, ctx, first.id)
    async def checkout(online_order_id: int | None, key: str) -> None:
        await SalesService(db_session).create_sale(
            ctx.store_id,
            ctx.clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=ctx.latte, qty=1)],
            tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(150))],
            service_mode=ServiceMode.TAKEOUT,
            idempotency_key=key,
            cart_session_id=cart_id,
            cart_revision=revision,
            online_order_id=online_order_id,
        )

    with pytest.raises(SignatureContentMismatch):
        await checkout(None if variant == "omitted" else other.id, f"bound-{variant}")
    assert (await _order_row(db_session, _rid(82))).payment_status != "PAID"
    # LINE Pay 也一樣：在任何請款之前就擋（這裡沒給 LINE Pay client，若沒擋會先撞別的錯）
    with pytest.raises(SignatureContentMismatch):
        await SalesService(db_session).create_sale(
            ctx.store_id,
            ctx.clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=ctx.latte, qty=1)],
            tenders=[
                TenderInput(
                    tender_type=TenderType.LINE_PAY,
                    amount=Decimal(150),
                    line_pay_one_time_key="OTK-bound",
                )
            ],
            service_mode=ServiceMode.TAKEOUT,
            idempotency_key=f"bound-lp-{variant}",
            cart_session_id=cart_id,
            cart_revision=revision,
            online_order_id=None if variant == "omitted" else other.id,
        )
    # 帶對的那張才收得了錢，並標成已付款
    await checkout(first.id, f"bound-ok-{variant}")
    assert (await _order_row(db_session, _rid(81))).payment_status == "PAID"


@pytest.mark.parametrize("first_online", [False, True])
async def test_idempotent_replay_rejects_changed_online_order(
    client: httpx.AsyncClient, db_session: AsyncSession, ctx: Ctx, first_online: bool
) -> None:
    first = await _pulled(db_session, ctx, _rid(71), [_line(1, ctx.latte, "拿鐵", 150)])
    second = await _pulled(db_session, ctx, _rid(72), [_line(1, ctx.latte, "拿鐵", 150)])
    body: dict[str, Any] = {
        "lines": [{"line_type": "MENU", "menu_item_id": ctx.latte, "qty": 1}],
        "service_mode": "TAKEOUT",
    }
    if first_online:
        body["online_order_id"] = first.id
    response = await client.post("/api/v1/sales", json=body, headers=_h(ctx.clerk, "online-replay"))
    assert response.status_code == 201, response.text
    replay = await client.post("/api/v1/sales", json=body, headers=_h(ctx.clerk, "online-replay"))
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == response.json()["id"]
    changed = await client.post(
        "/api/v1/sales",
        json={**body, "online_order_id": second.id},
        headers=_h(ctx.clerk, "online-replay"),
    )
    assert changed.status_code == 409, changed.text
