"""客顯購物車與簽署 TTL 的背景掃描。

惰性判定由 service 的讀取／轉移路徑負責；本迴圈是清除無人再讀取之殘留狀態的第二道保底。
"""

import asyncio
import contextlib
import logging

from app.core.db import get_sessionmaker
from app.modules.customerdisplay.service import CustomerDisplayService
from app.modules.signing.service import SigningService

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60


async def sweep_once() -> tuple[int, int, int]:
    factory = get_sessionmaker()
    async with factory() as session:
        carts = await CustomerDisplayService(session).sweep_expired_carts()
        signing = SigningService(session)
        tasks = await signing.sweep_expired_tasks()
        retention_due = await signing.report_due_signature_images()
        await session.commit()
        return carts, tasks, retention_due


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            carts, tasks, retention_due = await sweep_once()
            if carts or tasks or retention_due:
                logger.info(
                    "customer-display sweeper expired carts=%s tasks=%s retention_due=%s",
                    carts,
                    tasks,
                    retention_due,
                )
        except Exception:
            logger.exception("customer-display sweeper tick failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=SWEEP_INTERVAL_SECONDS,
            )
