"""客顯購物車與簽署 TTL 的背景掃描。

惰性判定由 service 的讀取／轉移路徑負責；本迴圈是清除無人再讀取之殘留狀態的第二道保底。
"""

import asyncio
import contextlib
import logging

from app.core.db import get_sessionmaker
from app.modules.customerdisplay.service import CustomerDisplayService

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60


async def sweep_once() -> int:
    factory = get_sessionmaker()
    async with factory() as session:
        count = await CustomerDisplayService(session).sweep_expired_carts()
        await session.commit()
        return count


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            expired = await sweep_once()
            if expired:
                logger.info("customer-display sweeper expired carts=%s", expired)
        except Exception:
            logger.exception("customer-display sweeper tick failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=SWEEP_INTERVAL_SECONDS,
            )
