"""發票上傳佇列的背景送出：業務順序與 transaction 邊界（scheduler 只負責定時喚醒）。

**為什麼需要這支**：F0401 開立由 `issue_for_sale` 在結帳後同步送出；但 F0501 作廢與
G0401 折讓只會被排進佇列，先前沒有任何東西會把它們送出去——後端啟動只跑備份與客顯兩個
排程，前端也沒有畫面呼叫 `/einvoice/queue/*`。實測後果：店長在系統裡作廢了發票，
平台上那張仍然有效（向 Amego 逐張查證 `invoice_status=99`、`cancel_date=0`），
帳上與申報對不起來，而且沒有任何畫面會提醒。

**刻意只送作廢與折讓**（`AUTO_SEND_ACTIONS`）：
- 開立牽涉字軌配號與客人當下要拿到的發票，事後自動補開可能在客人早就離店後才成立。
- 佇列裡可能留有大量歷史待開立列（本店 `lucamp_manual` 實測 17,692 筆種子殘留），
  無差別排空等於把一整年的舊發票補開到平台上。
開立的補救走人工（發票佇列頁的「重新開立」），由店長決定。

送出一律委派既有的 `EInvoiceService.send_via_amego`——認領、CAS 重取鎖、凍結 payload
與對帳先行等保護全部沿用，本層不重寫任何傳輸邏輯。
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.modules.einvoice.amego import AmegoClient
from app.modules.einvoice.service import EInvoiceService, build_amego_client
from app.shared.enums import EInvoiceAction

logger = logging.getLogger(__name__)

AUTO_SEND_ACTIONS = (EInvoiceAction.VOID, EInvoiceAction.ALLOWANCE)
"""可自動送出的動作。**不含 ISSUE**，理由見模組 docstring；改動請連同測試一起看。"""

AUTO_SEND_MAX_AGE = timedelta(days=7)
"""只自動送出這段時間內建立的佇列列。

越舊的待送出越可能是資料殘留或另有內情（平台早已作廢、單據已人工處理…），
自動補送的風險大於效益，交給人在發票佇列頁判斷。
"""

AUTO_SEND_RETRY_INTERVAL = timedelta(minutes=5)
"""**重試**的最短間隔（不套用在第一次嘗試）。

傳輸失敗會讓佇列列維持 PENDING（已認領），沒有間隔就會每個 tick 重擊平台一次。
但剛建立的列必須**下一輪就送**——那段等待期間平台上那張發票仍然有效，
為了退避而白等一個間隔，等於把風險窗口拉長。判別見 `list_due_auto_send_items`。
"""

AUTO_SEND_BATCH_SIZE = 20
"""單次掃描最多處理幾列；避免一次 tick 佔住連線與平台配額。"""

ClientFactory = Callable[[AsyncSession, int], Awaitable[AmegoClient]]


class EInvoiceBackgroundService:
    """執行發票佇列相關背景工作；scheduler 只負責定時喚醒此 service。"""

    @classmethod
    async def send_due_queue_items_once(
        cls,
        *,
        now: datetime | None = None,
        client_factory: ClientFactory | None = None,
    ) -> tuple[int, int]:
        """把到期的待送出佇列列送交平台，回 `(送出成功, 送出失敗)`。

        逐列各自一個 transaction：一列送不出去不得讓整輪停擺，否則一筆壞資料會卡住
        其後所有的作廢。失敗如實計數並記錄（不靜默略過），佇列列維持 PENDING 待下輪。
        """
        factory = get_sessionmaker()
        observed_at = now or datetime.now(UTC)
        build_client = client_factory or _default_client_factory
        async with factory() as session:
            due = await EInvoiceService(session).list_due_auto_send_items(
                actions=AUTO_SEND_ACTIONS,
                created_after=observed_at - AUTO_SEND_MAX_AGE,
                idle_since=observed_at - AUTO_SEND_RETRY_INTERVAL,
                limit=AUTO_SEND_BATCH_SIZE,
            )
            targets = [(item.store_id, item.id) for item in due]

        sent = failed = 0
        for store_id, queue_id in targets:
            try:
                async with factory() as session:
                    svc = EInvoiceService(session)
                    client = await build_client(session, store_id)
                    item = await svc.send_via_amego(store_id, queue_id, client=client)
                    await session.commit()
                if item.status.value == "UPLOADED":
                    sent += 1
                else:
                    failed += 1
                    logger.warning(
                        "einvoice auto-send rejected store=%s queue=%s status=%s",
                        store_id,
                        queue_id,
                        item.status.value,
                    )
            except Exception:
                failed += 1
                logger.exception(
                    "einvoice auto-send failed store=%s queue=%s", store_id, queue_id
                )
        return sent, failed


async def _default_client_factory(session: AsyncSession, store_id: int) -> AmegoClient:
    return await build_amego_client(session, store_id)
