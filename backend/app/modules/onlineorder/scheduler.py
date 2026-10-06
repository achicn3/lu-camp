"""線上訂單背景工作（docs/44 §5.3）：每幾秒拉單（兼心跳）、放掉到期的保留、送出回報佇列。

沒設定雲端（網址或密鑰空白）就什麼都不做。每一步各自 commit：拉單匯入成功了，回報失敗也不會
把匯入回滾；本層不承擔業務規則。
"""

import asyncio
import contextlib
import logging

from app.core.db import get_sessionmaker
from app.modules.onlineorder.availability_service import AvailabilityService
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.orders_service import OnlineOrdersService
from app.modules.onlineorder.router import get_online_order_client
from app.shared.exceptions import OnlineOrderPushFailed

logger = logging.getLogger(__name__)

PULL_INTERVAL_SECONDS = 5


async def _reconcile_availability(client: OnlineOrderClient) -> None:
    """Commit pending revision before HTTP so a lost ACK can retry the exact same body."""
    store_id = client.store_id
    async with get_sessionmaker()() as session:
        svc = AvailabilityService(session)
        payload = await svc.capture(store_id)
        await session.commit()
        if payload is None:
            return
        version = int(payload["menu_version"])
        revision = int(payload["revision"])
        try:
            code, error, current_revision = await client.put_availability(payload)
        except OnlineOrderPushFailed as exc:
            await svc.mark_error(store_id, version, revision, str(exc))
        else:
            if 200 <= code < 300:
                await svc.mark_delivered(store_id, version, revision)
            elif code == 409 and error == "version_conflict":
                await svc.mark_conflict(store_id, version, revision, error)
            elif code == 409 and error in {"stale_revision", "revision_conflict"}:
                await svc.mark_revision_conflict(
                    store_id, version, revision, current_revision or revision, error
                )
            else:
                await svc.mark_error(store_id, version, revision, error or f"HTTP {code}")
        await session.commit()


async def tick_once() -> None:
    client = get_online_order_client()
    if client is None:
        return
    store_id = client.store_id
    availability = asyncio.create_task(_reconcile_availability(client))
    try:
        async with get_sessionmaker()() as session:
            svc = OnlineOrdersService(session, client)
            result = await svc.pull_once(store_id)
            await session.commit()
            if result.imported:
                logger.info(
                    "online orders imported",
                    extra={
                        "imported": result.imported,
                        "held": result.held,
                        "rejected": result.rejected,
                    },
                )
            await svc.expire_reservations(store_id)
            await session.commit()
            await svc.flush_outbox(store_id)
            await session.commit()
    finally:
        try:
            await availability
        except Exception:
            logger.exception("online availability reconciliation failed")


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await tick_once()
        except Exception:
            logger.exception("online order tick failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=PULL_INTERVAL_SECONDS)
