"""線上訂單（店內端）業務邏輯（docs/44 §3.7、§4.3、§4.5、§4.6；O4b）。

- 拉單（每幾秒，兼心跳）：新單匯入一次（remote_id 唯一）；要確認庫存的單在**同一交易**內直接扣
  每日限量份數＝保留，回報 HELD；不夠就 REJECTED。回報一律先排進持久化佇列（本機先 commit）。
- 保留：現金單 30 分鐘沒來付就加回（單子不取消，之後來付時結帳會重新檢查份數）；取消加回；
  帶入結帳成立銷售時，結帳交易內先加回、再由一般結帳扣（淨額只扣一次、交易內持鎖）。
- 回報佇列：同一張單照先後送；連不上／雲端忙退避重試；雲端明確拒收（狀態不合法、找不到）不再重試。

跨模組只用 menu 的 service（CLAUDE.md §2）；銷售模組在結帳交易內呼叫
`begin_checkout`／`mark_settled`。
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import store_date, store_day_bounds, utc_now
from app.modules.menu.service import MenuService
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.models import (
    OnlineOrder,
    OnlineOrderLink,
    OnlineOrderOutbox,
    StockReservation,
)
from app.modules.onlineorder.orders_repository import OnlineOrdersRepository
from app.shared.enums import (
    OnlineOrderHold,
    OnlineOrderPayment,
    OnlineOrderSync,
    OnlineOutboxStatus,
    StockReservationStatus,
)
from app.shared.exceptions import (
    InsufficientStock,
    MenuItemNotFound,
    MenuItemUnavailable,
    OnlineOrderConflict,
    OnlineOrderNotConfigured,
    OnlineOrderNotFound,
    OnlineOrderPushFailed,
    SaleLineInvalid,
)

logger = logging.getLogger(__name__)

# 現金單保留份數多久（docs/44 §3.7）：到期只放掉保留，單子不取消。
RESERVATION_TTL = timedelta(minutes=30)
# 回報失敗的重試間隔（第 n 次失敗後等多久；超過就一直用最後一個）。
OUTBOX_BACKOFF = (
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
)
# 雲端回這些＝暫時性，晚點再送；其他 4xx＝明確拒收，不再重試。
_RETRY_ERRORS = frozenset({"conflict_retry"})
_REMOTE_ID = re.compile(r"^[0-9a-f]{32}$")
_HOLD_REQUESTED = "HOLD_REQUESTED"
_NOT_CONFIGURED = "尚未設定線上點餐（雲端網址或整合密鑰），請聯絡管理者"
_ERROR_MAX = 200


@dataclass(frozen=True)
class PullResult:
    imported: int = 0
    held: int = 0
    rejected: int = 0
    error: str | None = None


@dataclass(frozen=True)
class CartLine:
    menu_item_id: int
    menu_option_ids: list[int]
    qty: int
    description: str
    online_unit_price: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class OnlineCart:
    """帶入結帳的購物車：用 POS 目前的菜單重新計價，和客人看到的金額不同時店員看得出差額。"""

    order: OnlineOrder
    lines: list[CartLine]
    online_total: Decimal
    total: Decimal


@dataclass(frozen=True)
class OnlineOrdersOverview:
    orders: list[OnlineOrder]
    link: OnlineOrderLink | None
    configured: bool


class OnlineOrdersService:
    def __init__(self, session: AsyncSession, client: OnlineOrderClient | None) -> None:
        self._session = session
        self._client = client
        self._repo = OnlineOrdersRepository(session)
        self._menu = MenuService(session)

    def _require_client(self, store_id: int) -> OnlineOrderClient:
        if self._client is None or self._client.store_id != store_id:
            raise OnlineOrderNotConfigured(_NOT_CONFIGURED)
        return self._client

    async def _link(self, store_id: int) -> OnlineOrderLink:
        link = await self._repo.link(store_id)
        if link is None:
            link = OnlineOrderLink(store_id=store_id)
            self._repo.add(link)
        return link

    # ── 拉單與匯入 ──

    async def pull_once(self, store_id: int) -> PullResult:
        """拉一次新單並匯入；連不上只記錯誤給 POS 看，不丟例外（背景工作下次再拉）。"""
        client = self._require_client(store_id)
        link = await self._link(store_id)
        try:
            data = await client.pull_orders()
        except OnlineOrderPushFailed as exc:
            link.last_pull_error = str(exc)[:_ERROR_MAX]
            await self._repo.flush()
            return PullResult(error=link.last_pull_error)
        link.last_pull_at = utc_now()
        link.last_pull_error = None
        accepting = data.get("accepting")
        link.accepting = accepting if isinstance(accepting, bool) else None
        reason = data.get("paused_reason")
        link.paused_reason = reason[:100] if isinstance(reason, str) else None
        imported = held = rejected = 0
        raw_orders = data.get("orders")
        for raw in raw_orders if isinstance(raw_orders, list) else []:
            order = await self._import(store_id, raw)
            if order is None:
                continue
            imported += 1
            held += order.hold_status == OnlineOrderHold.HELD
            rejected += order.hold_status == OnlineOrderHold.REJECTED
        await self._repo.flush()
        return PullResult(imported=imported, held=held, rejected=rejected)

    async def _import(self, store_id: int, raw: object) -> OnlineOrder | None:
        """匯入一張；已經匯入過或格式不對回 None。整張在一個 savepoint 內，失敗不影響其他張。"""
        if not isinstance(raw, dict):
            return None
        remote_id = raw.get("id")
        if not isinstance(remote_id, str) or not _REMOTE_ID.match(remote_id):
            logger.warning("online order with bad id skipped")
            return None
        if await self._repo.by_remote_id(store_id, remote_id) is not None:
            return None
        try:
            order = _order_from_cloud(store_id, raw)
        except (KeyError, TypeError, ValueError):
            logger.warning("online order with bad shape skipped", extra={"remote_id": remote_id})
            return None
        async with self._session.begin_nested():
            self._repo.add(order)
            await self._repo.flush()
            report: dict[str, str] = {"sync_status": OnlineOrderSync.IMPORTED}
            if raw.get("hold_status") == _HOLD_REQUESTED:
                await self._reserve(store_id, order)
                report["hold_status"] = order.hold_status
            self._enqueue(order, report)
            await self._repo.flush()
        return order

    async def _reserve(self, store_id: int, order: OnlineOrder) -> None:
        """在同一交易內扣每日限量份數（保留）；任何一行不夠就整張不扣、標 REJECTED。"""
        consumed: list[dict[str, Any]] = []
        try:
            async with self._session.begin_nested():
                for line in order.lines:
                    item = await self._menu.get(store_id, int(line["item_id"]))
                    if item is None or item.archived_at is not None:
                        raise MenuItemNotFound(f"「{line['name']}」已不在菜單上")
                    if not item.is_available:
                        raise MenuItemUnavailable(f"「{item.name}」目前停售")
                    qty = int(line["qty"])
                    selection = await self._menu.price_selection(
                        store_id, item, [int(o) for o in line["option_ids"]], qty
                    )
                    used = await self._menu.consume_daily_stock(store_id, item, selection, qty)
                    if used:
                        consumed.append({"qty": qty, "consumed": used})
        except (InsufficientStock, MenuItemNotFound, MenuItemUnavailable, SaleLineInvalid) as exc:
            order.hold_status = OnlineOrderHold.REJECTED
            order.reject_reason = str(exc)[:300]
            return
        order.hold_status = OnlineOrderHold.HELD
        self._repo.add(
            StockReservation(
                store_id=store_id,
                online_order_id=order.id,
                consumed=consumed,
                status=StockReservationStatus.ACTIVE,
                expires_at=utc_now() + RESERVATION_TTL,
            )
        )

    async def _release(
        self, store_id: int, reservation: StockReservation, status: StockReservationStatus
    ) -> None:
        """把保留的份數加回（同一營業日、份數版本沒變才加；規則同作廢加回）。"""
        for row in reservation.consumed:
            await self._menu.restore_daily_stock(store_id, row["consumed"], qty=int(row["qty"]))
        reservation.status = status
        reservation.ended_at = utc_now()

    async def expire_reservations(self, store_id: int, *, now: datetime | None = None) -> int:
        """現金單保留到期就加回份數；單子不取消（客人之後來付，結帳會重新檢查份數）。"""
        moment = now or utc_now()
        expired = 0
        for order in await self._repo.expired_orders(store_id, moment):
            reservation = await self._repo.reservation(store_id, order.id, for_update=True)
            if (
                reservation is None
                or reservation.status != StockReservationStatus.ACTIVE
                or reservation.expires_at > moment
            ):
                continue
            await self._release(store_id, reservation, StockReservationStatus.EXPIRED)
            order.hold_status = OnlineOrderHold.NONE
            self._enqueue(order, {"hold_status": OnlineOrderHold.NONE})
            expired += 1
        await self._repo.flush()
        return expired

    # ── 回報佇列 ──

    def _enqueue(self, order: OnlineOrder, payload: dict[str, str]) -> None:
        self._repo.add(
            OnlineOrderOutbox(
                store_id=order.store_id,
                online_order_id=order.id,
                remote_id=order.remote_id,
                payload=payload,
                status=OnlineOutboxStatus.PENDING,
                attempts=0,
                next_attempt_at=utc_now(),
            )
        )

    async def flush_outbox(self, store_id: int, *, now: datetime | None = None) -> int:
        """送出到期的回報；回傳送成功幾筆。同一張單前一筆沒送成，後面的先不送（保持先後）。"""
        client = self._require_client(store_id)
        moment = now or utc_now()
        blocked: set[int] = set()
        sent = 0
        for row in await self._repo.pending_outbox(store_id):
            if row.online_order_id in blocked:
                continue
            if row.next_attempt_at > moment:
                blocked.add(row.online_order_id)
                continue
            try:
                code, error = await client.report_status(row.remote_id, row.payload)
            except OnlineOrderPushFailed as exc:
                self._retry_later(row, str(exc), moment)
                blocked.add(row.online_order_id)
                continue
            if code < 300:
                row.status = OnlineOutboxStatus.SENT
                row.sent_at = moment
                row.last_error = None
                sent += 1
            elif code >= 500 or code == 429 or error in _RETRY_ERRORS:
                self._retry_later(row, f"雲端暫時無法處理（{code} {error}）", moment)
                blocked.add(row.online_order_id)
            else:
                row.status = OnlineOutboxStatus.DEAD
                row.last_error = f"雲端拒收（{code} {error}）"[:_ERROR_MAX]
                blocked.add(row.online_order_id)
                logger.warning(
                    "online order report rejected",
                    extra={"remote_id": row.remote_id, "status": code, "error": error},
                )
        await self._repo.flush()
        return sent

    @staticmethod
    def _retry_later(row: OnlineOrderOutbox, reason: str, now: datetime) -> None:
        row.attempts += 1
        row.last_error = reason[:_ERROR_MAX]
        row.next_attempt_at = now + OUTBOX_BACKOFF[min(row.attempts, len(OUTBOX_BACKOFF)) - 1]

    # ── POS：清單、帶入結帳、取消、暫停接單 ──

    async def overview(self, store_id: int) -> OnlineOrdersOverview:
        start, _ = store_day_bounds(store_date(utc_now()))
        orders = list(await self._repo.list_since(store_id, start))
        return OnlineOrdersOverview(
            orders=orders,
            link=await self._repo.link(store_id),
            configured=self._client is not None and self._client.store_id == store_id,
        )

    async def _order(
        self, store_id: int, order_id: int, *, for_update: bool = False
    ) -> OnlineOrder:
        order = await self._repo.get(store_id, order_id, for_update=for_update)
        if order is None:
            raise OnlineOrderNotFound(f"找不到線上訂單 {order_id}")
        return order

    async def cart(self, store_id: int, order_id: int) -> OnlineCart:
        """帶入結帳的內容：照 POS 目前的菜單重新計價（不看剩幾份，結帳時才扣）。"""
        order = await self._order(store_id, order_id)
        self._ensure_open(order)
        lines: list[CartLine] = []
        for line in order.lines:
            item = await self._menu.get(store_id, int(line["item_id"]))
            if item is None or item.archived_at is not None:
                raise OnlineOrderConflict(f"「{line['name']}」已不在菜單上，請和客人確認後手動點")
            option_ids = [int(o) for o in line["option_ids"]]
            qty = int(line["qty"])
            try:
                selection = await self._menu.price_selection(
                    store_id, item, option_ids, qty, check_stock=False
                )
            except SaleLineInvalid as exc:
                raise OnlineOrderConflict(f"{exc}（菜單選項改過了，請和客人確認）") from exc
            lines.append(
                CartLine(
                    menu_item_id=item.id,
                    menu_option_ids=option_ids,
                    qty=qty,
                    description=selection.description,
                    online_unit_price=Decimal(line["unit_price"]),
                    unit_price=selection.unit_price,
                )
            )
        return OnlineCart(
            order=order,
            lines=lines,
            online_total=order.total,
            total=sum((line.unit_price * line.qty for line in lines), Decimal(0)),
        )

    @staticmethod
    def _ensure_open(order: OnlineOrder) -> None:
        if order.sync_status == OnlineOrderSync.SETTLED:
            raise OnlineOrderConflict("這張線上單已經結帳過了")
        if order.sync_status == OnlineOrderSync.VOIDED:
            raise OnlineOrderConflict("這張線上單已經取消了")
        if order.hold_status == OnlineOrderHold.REJECTED:
            raise OnlineOrderConflict("這張線上單庫存不足、已被拒絕，請請客人重新點")

    async def cancel(self, store_id: int, order_id: int, *, actor_user_id: int) -> OnlineOrder:
        """店員取消（客人沒來、點錯）：保留的份數加回，回報雲端已取消。已取消再按＝不動。"""
        order = await self._order(store_id, order_id, for_update=True)
        if order.sync_status == OnlineOrderSync.VOIDED:
            return order
        if order.sync_status == OnlineOrderSync.SETTLED:
            raise OnlineOrderConflict("這張線上單已經結帳，要退請到交易紀錄作廢或退貨")
        reservation = await self._repo.reservation(store_id, order.id, for_update=True)
        if reservation is not None and reservation.status == StockReservationStatus.ACTIVE:
            await self._release(store_id, reservation, StockReservationStatus.RELEASED)
        order.sync_status = OnlineOrderSync.VOIDED
        order.payment_status = OnlineOrderPayment.CANCELLED
        order.cancelled_at = utc_now()
        order.cancelled_by = actor_user_id
        self._enqueue(
            order,
            {"sync_status": OnlineOrderSync.VOIDED, "payment_status": OnlineOrderPayment.CANCELLED},
        )
        await self._repo.flush()
        return order

    async def begin_checkout(self, store_id: int, order_id: int) -> None:
        """結帳交易內、扣份數之前呼叫：鎖住這張單（同一張單只能成立一筆銷售），保留的份數先加回，
        接著由一般結帳照常扣——淨額只扣一次，交易內持鎖、別人插不進來。"""
        order = await self._order(store_id, order_id, for_update=True)
        self._ensure_open(order)
        reservation = await self._repo.reservation(store_id, order.id, for_update=True)
        if reservation is not None and reservation.status == StockReservationStatus.ACTIVE:
            await self._release(store_id, reservation, StockReservationStatus.CONVERTED)
        await self._repo.flush()

    async def mark_settled(self, store_id: int, order_id: int, *, sale_id: int) -> None:
        """銷售成立（同一交易）：線上單標已付款、掛上銷售單，回報雲端。"""
        order = await self._order(store_id, order_id, for_update=True)
        order.sync_status = OnlineOrderSync.SETTLED
        order.payment_status = OnlineOrderPayment.PAID
        order.sale_id = sale_id
        self._enqueue(
            order,
            {"sync_status": OnlineOrderSync.SETTLED, "payment_status": OnlineOrderPayment.PAID},
        )
        await self._repo.flush()

    async def set_accepting(self, store_id: int, accepting: bool) -> OnlineOrderLink:
        """暫停／恢復接單（雲端那邊生效）；恢復也會清掉雲端自動暫停的原因。"""
        data = await self._require_client(store_id).set_accepting(accepting)
        link = await self._link(store_id)
        value = data.get("accepting")
        link.accepting = value if isinstance(value, bool) else accepting
        reason = data.get("paused_reason")
        link.paused_reason = reason[:100] if isinstance(reason, str) else None
        await self._repo.flush()
        return link


def _order_from_cloud(store_id: int, raw: dict[str, Any]) -> OnlineOrder:
    """雲端拉來的單 → 本機列。格式不對丟 KeyError／TypeError／ValueError（整張跳過、記 log）。"""
    lines: list[dict[str, Any]] = [
        {
            "line_no": int(line["line_no"]),
            "item_id": int(line["item_id"]),
            "name": str(line["name"]),
            "option_ids": [int(o) for o in line["option_ids"]],
            "unit_price": int(line["unit_price"]),
            "qty": int(line["qty"]),
            "line_total": int(line["line_total"]),
            "limited": bool(line["limited"]),
        }
        for line in raw["lines"]
    ]
    if not lines or any(line["qty"] < 1 for line in lines):
        raise ValueError("empty or bad lines")
    mode = str(raw["service_mode"])
    if mode not in ("DINE_IN", "TAKEOUT"):
        raise ValueError("bad service mode")
    note = raw.get("note")
    table = raw.get("table_label")
    return OnlineOrder(
        store_id=store_id,
        remote_id=str(raw["id"]),
        table_label=str(table)[:20] if table else None,
        service_mode=mode,
        menu_version=int(raw["menu_version"]),
        total=Decimal(int(raw["total"])),
        payment_method=str(raw["payment_method"])[:10],
        note=str(note)[:200] if note else None,
        lines=lines,
        remote_created_at=datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00")),
        sync_status=OnlineOrderSync.IMPORTED,
        hold_status=OnlineOrderHold.NONE,
        payment_status=OnlineOrderPayment.UNPAID,
    )
