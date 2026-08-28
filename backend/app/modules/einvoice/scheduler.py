"""定時喚醒發票佇列背景送出；本層不承擔業務順序或 transaction。"""

import asyncio
import contextlib
import logging

from app.core.config import get_settings
from app.modules.einvoice.background_service import EInvoiceBackgroundService

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    """每分鐘把到期的作廢/折讓佇列列送交平台（主開關關閉時直接返回）。"""
    if not get_settings().einvoice_autosend_enabled:
        logger.info("einvoice auto-send disabled (einvoice_autosend_enabled=false)")
        return
    while not stop_event.is_set():
        try:
            sent, failed = await EInvoiceBackgroundService.send_due_queue_items_once()
            if sent or failed:
                logger.info("einvoice auto-send sent=%s failed=%s", sent, failed)
        except Exception:
            logger.exception("einvoice auto-send tick failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=SWEEP_INTERVAL_SECONDS)
