"""定時喚醒客顯背景 service；本層不承擔業務順序或 transaction。"""

import asyncio
import contextlib
import logging

from app.modules.customerdisplay.background_service import CustomerDisplayBackgroundService

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            carts, tasks, retention_due = await CustomerDisplayBackgroundService.sweep_once()
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
