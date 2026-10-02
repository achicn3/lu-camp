"""線上訂單背景工作（docs/44 §5.3）：每幾秒拉單（兼心跳）、放掉到期的保留、送出回報佇列。

沒設定雲端（網址或密鑰空白）就什麼都不做。每一步各自 commit：拉單匯入成功了，回報失敗也不會
把匯入回滾；本層不承擔業務規則。
"""

import asyncio
import contextlib
import logging

from app.core.db import get_sessionmaker
from app.modules.onlineorder.orders_service import OnlineOrdersService
from app.modules.onlineorder.router import get_online_order_client

logger = logging.getLogger(__name__)

PULL_INTERVAL_SECONDS = 5


async def tick_once() -> None:
    client = get_online_order_client()
    if client is None:
        return
    store_id = client.store_id
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


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await tick_once()
        except Exception:
            logger.exception("online order tick failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=PULL_INTERVAL_SECONDS)
