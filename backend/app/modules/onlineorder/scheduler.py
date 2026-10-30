"""線上訂單背景工作（docs/44 §5.3）：每幾秒拉單（兼心跳）、放掉到期的保留、送出回報佇列；
每分鐘把寄售帳篷清單推上官網（docs/97 §4.3，內容沒變就不推）。

沒設定雲端（網址或密鑰空白）就什麼都不做。每一步各自 commit：拉單匯入成功了，回報失敗也不會
把匯入回滾；本層不承擔業務規則。
"""

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime

from app.core.db import get_sessionmaker
from app.modules.onlineorder.availability_service import AvailabilityService
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.orders_service import OnlineOrdersService
from app.modules.onlineorder.router import get_online_order_client
from app.modules.onlineorder.tent_publish_service import TentPublishService
from app.shared.exceptions import OnlineOrderPushFailed

logger = logging.getLogger(__name__)

PULL_INTERVAL_SECONDS = 5
TENT_PUBLISH_INTERVAL_SECONDS = 60


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


async def publish_tents_once() -> None:
    """寄售帳篷清單有變就推上官網；雲端出錯只記在推送紀錄，下一輪再試。"""
    client = get_online_order_client()
    if client is None:
        return
    async with get_sessionmaker()() as session:
        result = await TentPublishService(session, client).publish(
            client.store_id, now=datetime.now(UTC)
        )
        await session.commit()
    if result.pushed:
        logger.info("consignment tents published")


async def _publish_tents_logged() -> None:
    try:
        await publish_tents_once()
    except Exception:
        logger.exception("consignment tents publish failed")


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    # 推帳篷另開一個工作跑：照片上傳可能很久，不能拖住拉單與心跳
    # （雲端 2 分鐘沒心跳就暫停線上接單）。
    # 上一輪還沒推完就不開新的一輪。
    tents_task: asyncio.Task[None] | None = None
    last_tents = float("-inf")
    try:
        while not stop_event.is_set():
            try:
                await tick_once()
            except Exception:
                logger.exception("online order tick failed")
            due = time.monotonic() - last_tents >= TENT_PUBLISH_INTERVAL_SECONDS
            if due and (tents_task is None or tents_task.done()):
                last_tents = time.monotonic()
                tents_task = asyncio.create_task(_publish_tents_logged())
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=PULL_INTERVAL_SECONDS)
    finally:
        if tents_task is not None and not tents_task.done():
            tents_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tents_task
