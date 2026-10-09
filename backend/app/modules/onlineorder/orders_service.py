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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import format_ntd
from app.core.time import store_date, store_day_bounds, utc_now
from app.modules.inventory.service import InventoryService
from app.modules.menu.service import MenuService
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.experience_service import MenuExperienceService
from app.modules.onlineorder.models import (
    OnlineOrder,
    OnlineOrderLink,
    OnlineOrderOutbox,
    StockReservation,
)
from app.modules.onlineorder.orders_repository import OnlineOrdersRepository
from app.modules.sales.inputs import (
    CARRIER_TYPE_MOBILE,
    InvoiceInfoInput,
    OnlineLinePayCapture,
    SaleLineInput,
    TenderInput,
)
from app.modules.settings.service import StoreSettingsService
from app.shared.enums import (
    OnlineOrderFulfillment,
    OnlineOrderHold,
    OnlineOrderPayment,
    OnlineOrderSync,
    OnlineOutboxStatus,
    OnlineRefundStatus,
    SaleLineType,
    ServiceMode,
    StockReservationStatus,
    TenderType,
)
from app.shared.exceptions import (
    CrossStoreReference,
    DomainError,
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
# 線上 LINE Pay 單保留 10 分鐘（docs/44 §3.7）：客人在手機上付，過期雲端就不請款（C10）。
LINEPAY_RESERVATION_TTL = timedelta(minutes=10)
_LINE_PAY = "LINE_PAY"
_TX_ID = re.compile(r"^\d{1,20}$")
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
    line_no: int
    """雲端訂單的行號：同品項同選項可能有兩行（體驗卡＋一般點），POS 靠它分開。"""
    line_type: str
    """MENU（餐飲）或 CATALOG（帶回家商品，docs/63 §13）。"""
    menu_item_id: int | None
    catalog_product_id: int | None
    menu_option_ids: list[int]
    experience_id: int | None
    qty: int
    description: str
    online_unit_price: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class SettleResult:
    """線上 LINE Pay 已付款單的成立結果：成立了有 sale_id；沒辦法自動成立有 attention（原因）。"""

    sale_id: int | None
    attention: str | None


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
        self._inventory = InventoryService(session)
        self._experiences = MenuExperienceService(session)

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
        existing = await self._repo.by_remote_id(store_id, remote_id)
        if existing is not None:
            # 已匯入、後來才用 LINE Pay 付清的單：記下付款資料，POS 頁面據此成立銷售（O5b）。
            payment = _paid_linepay(raw)
            if payment is not None and existing.linepay_transaction_id is None:
                existing.linepay_order_id, existing.linepay_transaction_id = payment
                await self._repo.flush()
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
                expires_at = await self._reserve(store_id, order)
                report["hold_status"] = order.hold_status
                if expires_at is not None:
                    # 雲端在到期前就不再請款，不靠之後的到期回報準時送達（Codex O5 第一輪）。
                    report["hold_expires_at"] = expires_at.isoformat()
            self._enqueue(order, report)
            await self._repo.flush()
        return order

    async def _reserve(self, store_id: int, order: OnlineOrder) -> datetime | None:
        """在同一交易內扣每日限量份數（保留）；任何一行不夠就整張不扣、標 REJECTED。"""
        consumed: list[dict[str, Any]] = []
        try:
            async with self._session.begin_nested():
                for line in order.lines:
                    if _is_retail(line):
                        # 帶回家商品：直接扣現量（櫃檯就賣不掉這幾件）；加回時照數量加。
                        product_id, qty = int(line["catalog_product_id"]), int(line["qty"])
                        await self._inventory.hold_catalog_for_online_order(
                            store_id, product_id, qty, online_order_id=order.id
                        )
                        consumed.append({"qty": qty, "catalog_product_id": product_id})
                        continue
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
        except (
            CrossStoreReference,
            InsufficientStock,
            MenuItemNotFound,
            MenuItemUnavailable,
            SaleLineInvalid,
        ) as exc:
            order.hold_status = OnlineOrderHold.REJECTED
            order.reject_reason = str(exc)[:300]
            return None
        order.hold_status = OnlineOrderHold.HELD
        expires_at = utc_now() + _reservation_ttl(order)
        self._repo.add(
            StockReservation(
                store_id=store_id,
                online_order_id=order.id,
                consumed=consumed,
                status=StockReservationStatus.ACTIVE,
                expires_at=expires_at,
            )
        )
        return expires_at

    async def _release(
        self, store_id: int, reservation: StockReservation, status: StockReservationStatus
    ) -> None:
        """把保留的份數加回（同一營業日、份數版本沒變才加；規則同作廢加回）。"""
        for row in reservation.consumed:
            if "catalog_product_id" in row:
                await self._inventory.release_online_hold(
                    store_id,
                    int(row["catalog_product_id"]),
                    int(row["qty"]),
                    online_order_id=reservation.online_order_id,
                )
                continue
            await self._menu.restore_daily_stock(store_id, row["consumed"], qty=int(row["qty"]))
        reservation.status = status
        reservation.ended_at = utc_now()

    async def expire_reservations(self, store_id: int, *, now: datetime | None = None) -> int:
        """現金單保留到期就加回份數；單子不取消（客人之後來付，結帳會重新檢查份數）。"""
        moment = now or utc_now()
        expired = 0
        link = await self._repo.link(store_id)
        last_heard = link.last_pull_at if link is not None else None
        for order in await self._repo.expired_orders(store_id, moment):
            # 可能已扣款的商品不能釋放給下一位客人；明確未付款後才恢復到期處理。
            if await self._payment_pending(store_id, order.id):
                continue
            # 客人已在線上用 LINE Pay 付清（POS 頁面還沒開來成立銷售）：份數要留給他
            # （Codex O5 第三輪）。
            if order.linepay_transaction_id is not None:
                continue
            reservation = await self._repo.reservation(store_id, order.id, for_update=True)
            if (
                reservation is None
                or reservation.status != StockReservationStatus.ACTIVE
                or reservation.expires_at > moment
            ):
                continue
            # LINE Pay 單：雲端在到期前 1 分鐘就不再請款，所以要「到期之後成功拉過一次單、
            # 還沒看到付款」才確定客人沒付；連不上雲端時寧可多留（Codex O5 第四輪）。
            if order.payment_method == _LINE_PAY and (
                last_heard is None or last_heard <= reservation.expires_at
            ):
                continue
            await self._release(store_id, reservation, StockReservationStatus.EXPIRED)
            order.hold_status = OnlineOrderHold.NONE
            self._enqueue(order, {"hold_status": OnlineOrderHold.NONE})
            expired += 1
        await self._repo.flush()
        return expired

    # ── 回報佇列 ──

    def _enqueue(self, order: OnlineOrder, payload: Mapping[str, str | int]) -> None:
        self._repo.add(
            OnlineOrderOutbox(
                store_id=order.store_id,
                online_order_id=order.id,
                remote_id=order.remote_id,
                payload=dict(payload),
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
        if order.payment_method == _LINE_PAY:
            # 客人選了線上 LINE Pay：在這裡收現金，客人那邊又付成功就會收兩次錢。
            raise OnlineOrderConflict(
                "這張單客人選了 LINE Pay，付好會自動成立；客人想改付現金，請先取消這張再重新點"
            )
        lines: list[CartLine] = []
        for line in order.lines:
            if _is_retail(line):
                lines.append(await self._retail_cart_line(store_id, line))
                continue
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
            experience_id = line.get("experience_id")
            lines.append(
                CartLine(
                    line_no=int(line["line_no"]),
                    line_type="MENU",
                    menu_item_id=item.id,
                    catalog_product_id=None,
                    menu_option_ids=option_ids,
                    experience_id=experience_id,
                    qty=qty,
                    description=await self._describe(
                        store_id, selection.description, experience_id, str(line["name"])
                    ),
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

    async def _retail_cart_line(self, store_id: int, line: dict[str, Any]) -> CartLine:
        """帶回家商品照目前售價帶入；商品沒了或停售就請店員和客人確認。"""
        product = await self._inventory.get_catalog(store_id, int(line["catalog_product_id"]))
        if product is None or not product.is_active:
            raise OnlineOrderConflict(f"「{line['name']}」已停售，請和客人確認後手動點")
        return CartLine(
            line_no=int(line["line_no"]),
            line_type="CATALOG",
            menu_item_id=None,
            catalog_product_id=product.id,
            menu_option_ids=[],
            experience_id=None,
            qty=int(line["qty"]),
            description=product.name,
            online_unit_price=Decimal(line["unit_price"]),
            unit_price=product.unit_price,
        )

    @staticmethod
    def _ensure_open(order: OnlineOrder) -> None:
        if order.sync_status == OnlineOrderSync.SETTLED:
            raise OnlineOrderConflict("這張線上單已經結帳過了")
        if order.sync_status == OnlineOrderSync.VOIDED:
            raise OnlineOrderConflict("這張線上單已經取消了")
        if order.hold_status == OnlineOrderHold.REJECTED:
            raise OnlineOrderConflict("這張線上單庫存不足、已被拒絕，請請客人重新點")

    async def _payment_pending(
        self, store_id: int, order_id: int, *, excluding_cart_id: int | None = None
    ) -> bool:
        from app.modules.customerdisplay.service import CustomerDisplayService

        return await CustomerDisplayService(self._session).has_pending_online_payment(
            store_id, order_id, excluding_cart_id=excluding_cart_id
        )

    async def check_payment_available(
        self, store_id: int, order_id: int, *, cart_session_id: int | None = None
    ) -> None:
        """Lock the open order and reject a payment owned by another cart."""
        # 與取消／到期共用訂單鎖；購物車先鎖、訂單後鎖，且此處只讀其他購物車狀態。
        order = await self._order(store_id, order_id, for_update=True)
        self._ensure_open(order)
        if await self._payment_pending(store_id, order_id, excluding_cart_id=cart_session_id):
            raise OnlineOrderConflict("此線上單正在付款或付款結果待確認，請先完成原櫃檯的付款對帳")

    async def cancel(self, store_id: int, order_id: int, *, actor_user_id: int) -> OnlineOrder:
        """店員取消（客人沒來、點錯）：保留的份數加回，回報雲端已取消。已取消再按＝不動。"""
        order = await self._order(store_id, order_id, for_update=True)
        if order.sync_status == OnlineOrderSync.VOIDED:
            return order
        if order.sync_status == OnlineOrderSync.SETTLED:
            raise OnlineOrderConflict("這張線上單已經結帳，要退請到交易紀錄作廢或退貨")
        if await self._payment_pending(store_id, order_id):
            raise OnlineOrderConflict(
                "此線上單正在付款或付款結果待確認，不能取消；請先完成付款對帳"
            )
        payload: dict[str, str] = {
            "sync_status": OnlineOrderSync.VOIDED,
            "payment_status": OnlineOrderPayment.CANCELLED,
        }
        if order.payment_method == _LINE_PAY:
            # 客人可能正在手機上付：先請雲端取消（雲端說已在請款／已付款就擋），成功才在店內取消。
            # 先在店內取消、雲端晚幾秒才知道，客人剛好付成功就收了錢卻沒有銷售（Codex O5 第一輪）。
            await self._cancel_in_cloud(store_id, order, payload)
        reservation = await self._repo.reservation(store_id, order.id, for_update=True)
        if reservation is not None and reservation.status == StockReservationStatus.ACTIVE:
            await self._release(store_id, reservation, StockReservationStatus.RELEASED)
        order.sync_status = OnlineOrderSync.VOIDED
        order.payment_status = OnlineOrderPayment.CANCELLED
        order.cancelled_at = utc_now()
        order.cancelled_by = actor_user_id
        if order.payment_method != _LINE_PAY:
            self._enqueue(order, payload)
        await self._repo.flush()
        return order

    async def held_portions(self, store_id: int, order_id: int) -> dict[tuple[str, int], int]:
        """這張線上單**今天**還保留著的份數，依（每日限量對象, id）加總。

        POS／顧客螢幕試算帶入這張單時，它自己保留的份數算可用（否則保留了最後一份就試算售完、
        結帳鈕永遠按不下去；Codex O4 第二輪）。唯讀——不釋放保留，真正結帳仍在交易內嚴格檢查。
        昨天的保留今天已經歸零，不算。
        """
        reservation = await self._repo.reservation(store_id, order_id)
        if reservation is None or reservation.status != StockReservationStatus.ACTIVE:
            return {}
        day = store_date(utc_now()).isoformat()
        held: dict[tuple[str, int], int] = {}
        for row in reservation.consumed:
            for entry in row.get("consumed", []):
                if str(entry["day"]) != day:
                    continue
                key = (str(entry["kind"]), int(entry["id"]))
                held[key] = held.get(key, 0) + int(row["qty"])
        return held

    async def begin_checkout(
        self, store_id: int, order_id: int, *, cart_session_id: int | None = None
    ) -> None:
        """結帳交易內、扣份數之前呼叫：鎖住這張單（同一張單只能成立一筆銷售），保留的份數先加回，
        接著由一般結帳照常扣——淨額只扣一次，交易內持鎖、別人插不進來。"""
        await self.check_payment_available(store_id, order_id, cart_session_id=cart_session_id)
        reservation = await self._repo.reservation(store_id, order_id, for_update=True)
        if reservation is not None and reservation.status == StockReservationStatus.ACTIVE:
            await self._release(store_id, reservation, StockReservationStatus.CONVERTED)
        await self._repo.flush()

    async def mark_settled(
        self,
        store_id: int,
        order_id: int,
        *,
        sale_id: int,
        take_home: Sequence[tuple[int, int]] = (),
    ) -> None:
        """銷售成立（同一交易）：線上單標已付款、掛上銷售單，回報雲端。"""
        order = await self._order(store_id, order_id, for_update=True)
        order.sync_status = OnlineOrderSync.SETTLED
        order.payment_status = OnlineOrderPayment.PAID
        order.sale_id = sale_id
        report: dict[str, str] = {
            "sync_status": OnlineOrderSync.SETTLED,
            "payment_status": OnlineOrderPayment.PAID,
        }
        # 有帶回家商品：付了錢還要等店員交貨才算結單（docs/63 §13）。要交的照**實際結帳**的
        # 一般商品（`take_home`：(商品, 數量)）——客人在櫃檯改數量或不買了，清單跟著變。
        if take_home and any(_is_retail(line) for line in order.lines):
            order.fulfillment_status = OnlineOrderFulfillment.AWAITING
            order.handover_items = [
                {
                    "catalog_product_id": product_id,
                    "name": await self._product_name(store_id, product_id),
                    "qty": qty,
                }
                for product_id, qty in take_home
            ]
            report["fulfillment"] = OnlineOrderFulfillment.AWAITING
        self._enqueue(order, report)
        await self._repo.flush()

    async def _product_name(self, store_id: int, product_id: int) -> str:
        product = await self._inventory.get_catalog(store_id, product_id)
        return product.name if product is not None else f"商品 {product_id}"

    async def sale_voided(self, store_id: int, sale_id: int, *, refunded_amount: Decimal) -> None:
        """銷售作廢（同一交易）：掛著它的線上單回報雲端已退款；還沒交的帶回家商品也不能再交
        （Codex M1d 第一輪）。"""
        order = await self._repo.by_sale(store_id, sale_id)
        if order is None:
            return
        if order.fulfillment_status == OnlineOrderFulfillment.AWAITING:
            order.fulfillment_status = OnlineOrderFulfillment.NONE
            order.handover_items = None
        self._report_refund(order, refunded_amount, fully=True)
        await self._repo.flush()

    async def sale_refunded(
        self, store_id: int, sale_id: int, *, refunded_amount: Decimal, fully: bool
    ) -> None:
        """退貨成立（同一交易）：回報雲端累計退了多少，全退完＝已退款，否則部分退款。"""
        order = await self._repo.by_sale(store_id, sale_id)
        if order is None:
            return
        self._report_refund(order, refunded_amount, fully=fully)
        await self._repo.flush()

    def _report_refund(self, order: OnlineOrder, refunded_amount: Decimal, *, fully: bool) -> None:
        status = OnlineRefundStatus.REFUNDED if fully else OnlineRefundStatus.PARTIALLY_REFUNDED
        self._enqueue(order, {"payment_status": status, "refunded_amount": int(refunded_amount)})

    async def settle_paid(
        self, store_id: int, order_id: int, *, actor_user_id: int
    ) -> SettleResult:
        """客人在線上已用 LINE Pay 付清：由開著的 POS 頁面成立銷售（docs/44 §4.4.2）。

        以客人已付金額為準、一律原價不套活動；POS 現在的價格和客人付的不同就**不成立**、
        把原因記在單上給店員處理（回 `attention`）。已成立過的直接回原銷售
        （兩台 POS 同時看到也只成立一筆）。
        LINE Pay 只記帳、不再扣款；發票照客人填的手機條碼／統編開，沒填印紙本。
        """
        # 函式內 import：sales 已在模組層 import 本模組（結帳時呼叫 begin_checkout／
        # mark_settled），打破循環。
        from app.modules.sales.service import SalesService

        order = await self._order(store_id, order_id, for_update=True)
        if order.sync_status == OnlineOrderSync.SETTLED and order.sale_id is not None:
            return SettleResult(sale_id=order.sale_id, attention=None)
        if order.sync_status == OnlineOrderSync.VOIDED:
            raise OnlineOrderConflict("這張線上單已經取消了")
        if (
            order.payment_method != _LINE_PAY
            or order.linepay_transaction_id is None
            or order.linepay_order_id is None
        ):
            raise OnlineOrderConflict("這張線上單還沒用 LINE Pay 付款")
        sales = SalesService(self._session)
        lines = [_sale_line(line) for line in order.lines]
        # 沒開電子發票的店不能帶發票欄位（照櫃檯結帳的規則）：客人填的載具就用不到了。
        einvoice = (
            await StoreSettingsService(self._session).get_effective_settings(store_id)
        ).einvoice_enabled
        overrides = await sales.online_campaign_overrides(store_id)
        quote = await sales.quote_sale(
            store_id, lines=lines, disabled_campaigns=overrides, online_order_id=order.id
        )
        if quote.total != order.total:
            order.attention = (
                f"客人用 LINE Pay 付了 {format_ntd(order.total)} 元，"
                f"POS 現在算 {format_ntd(quote.total)} 元"
                "（菜單改過價）；沒有自動成立，請到 LINE Pay 後台核對後手動處理"
            )[:300]
            await self._repo.flush()
            return SettleResult(sale_id=None, attention=order.attention)
        try:
            # 成立失敗（例如櫃檯剛好把最後一件賣掉）整筆退回 savepoint、原因記在單上給店員看。
            async with self._session.begin_nested():
                sale = await sales.create_sale(
                    store_id,
                    actor_user_id,
                    lines=lines,
                    tenders=[TenderInput(tender_type=TenderType.LINE_PAY, amount=order.total)],
                    idempotency_key=f"online-linepay-{order.remote_id}",
                    disabled_campaigns=overrides,
                    service_mode=ServiceMode(order.service_mode),
                    table_no=order.table_label if order.service_mode == "DINE_IN" else None,
                    invoice_info=_invoice_info(order) if einvoice else None,
                    online_order_id=order.id,
                    online_linepay=OnlineLinePayCapture(
                        order_id=order.linepay_order_id,
                        transaction_id=order.linepay_transaction_id,
                        amount=order.total,
                    ),
                )
        except DomainError as exc:
            order.attention = f"客人已用 LINE Pay 付款，但沒辦法自動成立：{exc}"[:300]
            await self._repo.flush()
            return SettleResult(sale_id=None, attention=order.attention)
        order.attention = None
        await self._repo.flush()
        return SettleResult(sale_id=sale.id, attention=None)

    async def _cancel_in_cloud(
        self, store_id: int, order: OnlineOrder, payload: dict[str, str]
    ) -> None:
        client = self._require_client(store_id)
        try:
            code, error = await client.report_status(order.remote_id, payload)
        except OnlineOrderPushFailed as exc:
            raise OnlineOrderConflict(
                "連不上線上點餐雲端，LINE Pay 的單暫時不能取消，請稍後再試"
            ) from exc
        if code == 409 and error == "invalid_transition":
            raise OnlineOrderConflict(
                "客人正在用 LINE Pay 付款或已經付好了，不能取消；付好會自動成立，請稍候"
            )
        if code >= 300:
            raise OnlineOrderConflict(f"線上點餐雲端沒有接受取消（{code} {error}），請稍後再試")

    async def hand_over(self, store_id: int, order_id: int, *, actor_user_id: int) -> OnlineOrder:
        """店員把帶回家商品交給客人：結單、寫稽核、回報雲端（客人頁顯示已領取）。重按＝不動。"""
        order = await self._order(store_id, order_id, for_update=True)
        if order.fulfillment_status == OnlineOrderFulfillment.HANDED_OVER:
            return order
        if order.fulfillment_status != OnlineOrderFulfillment.AWAITING:
            raise OnlineOrderConflict("這張線上單還沒收款，或沒有要交給客人的商品；請先帶入結帳")
        order.fulfillment_status = OnlineOrderFulfillment.HANDED_OVER
        order.handed_over_at = utc_now()
        order.handed_over_by = actor_user_id
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="HAND_OVER_ONLINE_ORDER",
            entity_type="online_order",
            entity_id=str(order.id),
            before={"fulfillment_status": OnlineOrderFulfillment.AWAITING.value},
            after={"fulfillment_status": OnlineOrderFulfillment.HANDED_OVER.value},
        )
        self._enqueue(order, {"fulfillment": OnlineOrderFulfillment.HANDED_OVER})
        await self._repo.flush()
        return order

    async def _describe(
        self, store_id: int, description: str, experience_id: int | None, ordered_name: str
    ) -> str:
        """體驗卡的行冠上卡片標題（店員才知道要帶體驗）；卡片已刪就沿用客人送單時的品名。"""
        if experience_id is None:
            return description
        title = await self._experiences.title_of(store_id, experience_id)
        return ordered_name if title is None else f"{title}・{description}"

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
            # 餐飲行帶 item_id；帶回家商品行帶 catalog_product_id（docs/63 §13），兩者擇一。
            **(
                {"catalog_product_id": int(line["catalog_product_id"])}
                if line.get("catalog_product_id") is not None
                else {"item_id": int(line["item_id"])}
            ),
            "name": str(line["name"]),
            "option_ids": [int(o) for o in line["option_ids"]],
            "unit_price": int(line["unit_price"]),
            "qty": int(line["qty"]),
            "line_total": int(line["line_total"]),
            "limited": bool(line["limited"]),
            # 體驗卡來的行（M1c）；舊版雲端沒有這個鍵
            **(
                {"experience_id": int(line["experience_id"])}
                if line.get("experience_id") is not None
                else {}
            ),
        }
        for line in raw["lines"]
    ]
    if not lines or any(line["qty"] < 1 for line in lines):
        raise ValueError("empty or bad lines")
    # 帶回家商品沒有選項、不會是體驗卡。
    if any(_is_retail(line) and (line["option_ids"] or "experience_id" in line) for line in lines):
        raise ValueError("retail line with options")
    mode = str(raw["service_mode"])
    if mode not in ("DINE_IN", "TAKEOUT"):
        raise ValueError("bad service mode")
    note = raw.get("note")
    table = raw.get("table_label")
    paid = _paid_linepay(raw)
    invoice = _invoice(raw)
    return OnlineOrder(
        store_id=store_id,
        remote_id=str(raw["id"]),
        table_label=str(table)[:20] if table else None,
        service_mode=mode,
        menu_version=int(raw["menu_version"]),
        total=Decimal(int(raw["total"])),
        payment_method=str(raw["payment_method"])[:10],
        linepay_order_id=paid[0] if paid else None,
        linepay_transaction_id=paid[1] if paid else None,
        invoice_carrier=invoice[0],
        invoice_tax_id=invoice[1],
        note=str(note)[:200] if note else None,
        lines=lines,
        remote_created_at=datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00")),
        sync_status=OnlineOrderSync.IMPORTED,
        hold_status=OnlineOrderHold.NONE,
        payment_status=OnlineOrderPayment.UNPAID,
    )


def _is_retail(line: dict[str, Any]) -> bool:
    """這一行是帶回家商品（一般商品）而不是餐飲。"""
    return line.get("catalog_product_id") is not None


def _paid_linepay(raw: dict[str, Any]) -> tuple[str, str] | None:
    """雲端回報 LINE Pay 已付款：（LINE Pay 訂單號, 交易號）。交易號一律是字串（19 位數字）。"""
    payment = raw.get("payment")
    if raw.get("payment_status") != "PAID" or not isinstance(payment, dict):
        return None
    order_id, tx = payment.get("order_id"), payment.get("transaction_id")
    if not isinstance(order_id, str) or not isinstance(tx, str) or not _TX_ID.match(tx):
        return None
    if int(payment.get("amount", -1)) != int(raw["total"]):
        return None
    return order_id[:64], tx


def _invoice(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    """客人填的發票資料（手機條碼, 統編）；格式不對就當沒填（印紙本）。"""
    invoice = raw.get("invoice")
    if not isinstance(invoice, dict):
        return None, None
    carrier, tax_id = invoice.get("carrier"), invoice.get("tax_id")
    carrier_ok = isinstance(carrier, str) and re.fullmatch(r"/[0-9A-Z.+-]{7}", carrier)
    tax_ok = isinstance(tax_id, str) and re.fullmatch(r"\d{8}", tax_id)
    return (carrier if carrier_ok else None), (tax_id if tax_ok else None)


def _reservation_ttl(order: OnlineOrder) -> timedelta:
    return LINEPAY_RESERVATION_TTL if order.payment_method == _LINE_PAY else RESERVATION_TTL


def _sale_line(line: dict[str, Any]) -> SaleLineInput:
    """線上單的一行 → 銷售明細（餐飲＋選項，或帶回家的一般商品）。"""
    if _is_retail(line):
        return SaleLineInput(
            line_type=SaleLineType.CATALOG,
            catalog_product_id=int(line["catalog_product_id"]),
            qty=int(line["qty"]),
        )
    return SaleLineInput(
        line_type=SaleLineType.MENU,
        menu_item_id=int(line["item_id"]),
        qty=int(line["qty"]),
        menu_option_ids=tuple(int(o) for o in line["option_ids"]),
    )


def _invoice_info(order: OnlineOrder) -> InvoiceInfoInput | None:
    """客人填的手機條碼或統編；都沒填＝印紙本證明聯。"""
    if order.invoice_tax_id is not None:
        return InvoiceInfoInput(buyer_tax_id=order.invoice_tax_id)
    if order.invoice_carrier is not None:
        return InvoiceInfoInput(carrier_type=CARRIER_TYPE_MOBILE, carrier_id=order.invoice_carrier)
    return None
