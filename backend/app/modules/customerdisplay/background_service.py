"""客顯背景工作的業務順序與 transaction 邊界。"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.modules.customerdisplay.service import CustomerDisplayService
from app.modules.returns.service import ReturnsService
from app.modules.signing.service import SigningService

logger = logging.getLogger(__name__)

AUTO_RECONCILE_WINDOW = timedelta(minutes=15)
AUTO_RECONCILE_BATCH_SIZE = 50
AUTO_REFUND_RECOVERY_BATCH_SIZE = 20


class CustomerDisplayBackgroundService:
    """執行客顯相關背景工作；scheduler 只負責定時喚醒此 service。"""

    @staticmethod
    async def reconcile_uncertain_payment_target(
        session: AsyncSession,
        *,
        store_id: int,
        terminal_id: int,
        linepay_client: object,
    ) -> str:
        """查同一筆 LINE Pay 原單並提交本地結果，絕不重新請款。"""
        outcome, _cart = await CustomerDisplayService(session).reconcile_payment(
            store_id,
            terminal_id,
            action="QUERY_PROVIDER",
            actor_user_id=None,
            linepay_client=linepay_client,
            reason=None,
            evidence_type=None,
            evidence_reference=None,
        )
        await session.commit()
        return outcome

    @classmethod
    async def reconcile_uncertain_payments_once(
        cls, *, now: datetime | None = None
    ) -> tuple[int, int]:
        """逐筆 transaction 自動查詢近期付款結果不明的原單。"""
        linepay_client = CustomerDisplayService.configured_linepay_client()
        if linepay_client is None:
            return 0, 0
        factory = get_sessionmaker()
        observed_at = now or datetime.now(UTC)
        async with factory() as session:
            targets = await CustomerDisplayService(session).list_recent_payment_uncertain_targets(
                observed_at - AUTO_RECONCILE_WINDOW,
                limit=AUTO_RECONCILE_BATCH_SIZE,
            )
        checked = 0
        resolved = 0
        for store_id, terminal_id in targets:
            async with factory() as session:
                try:
                    outcome = await cls.reconcile_uncertain_payment_target(
                        session,
                        store_id=store_id,
                        terminal_id=terminal_id,
                        linepay_client=linepay_client,
                    )
                    checked += 1
                    if outcome != "STILL_UNCERTAIN":
                        resolved += 1
                except Exception:
                    await session.rollback()
                    logger.exception(
                        "automatic LINE Pay reconciliation failed store=%s terminal=%s",
                        store_id,
                        terminal_id,
                    )
        return checked, resolved

    @staticmethod
    async def _recover_refund_attempt(session: AsyncSession, attempt_id: int) -> bool:
        if not await ReturnsService(session).recover_succeeded_linepay_refund(attempt_id):
            return False
        await session.commit()
        return True

    @classmethod
    async def recover_succeeded_linepay_refunds_once(cls) -> tuple[int, int]:
        """逐筆補完平台已退款的本地退貨；絕不再次呼叫平台退款。"""
        factory = get_sessionmaker()
        async with factory() as session:
            attempt_ids = await ReturnsService(session).list_succeeded_linepay_recovery_ids(
                limit=AUTO_REFUND_RECOVERY_BATCH_SIZE
            )
        checked = 0
        recovered = 0
        for attempt_id in attempt_ids:
            checked += 1
            async with factory() as session:
                try:
                    if await cls._recover_refund_attempt(session, attempt_id):
                        recovered += 1
                except Exception as exc:
                    await session.rollback()
                    logger.exception(
                        "automatic LINE Pay local refund recovery failed attempt=%s", attempt_id
                    )
                    async with factory() as error_session:
                        if await ReturnsService(error_session).mark_linepay_recovery_failed(
                            attempt_id,
                            error_type=type(exc).__name__,
                        ):
                            await error_session.commit()
        return checked, recovered

    @classmethod
    async def sweep_once(cls) -> tuple[int, int, int]:
        """依不可逆性排序執行一次背景維護，並各自完成所需 transaction。"""
        # 外部退款成功是不可逆事實；先補本地，再做 SIGNED TTL 清理，避免同一 tick 先把
        # 復原所需的精確同意標成 EXPIRED。先前 tick 已逾期者仍由復原入口逐欄核對後消費。
        refund_checked, refund_recovered = await cls.recover_succeeded_linepay_refunds_once()
        if refund_checked or refund_recovered:
            logger.info(
                "automatic LINE Pay refund recovery checked=%s recovered=%s",
                refund_checked,
                refund_recovered,
            )

        factory = get_sessionmaker()
        async with factory() as session:
            carts = await CustomerDisplayService(session).sweep_expired_carts()
            signing = SigningService(session)
            tasks = await signing.sweep_expired_tasks()
            retention_due = await signing.report_due_signature_images()
            await session.commit()

        checked, resolved = await cls.reconcile_uncertain_payments_once()
        if checked or resolved:
            logger.info(
                "automatic LINE Pay reconciliation checked=%s resolved=%s",
                checked,
                resolved,
            )
        return carts, tasks, retention_due
