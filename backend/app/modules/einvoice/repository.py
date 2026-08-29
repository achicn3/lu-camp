"""einvoice 資料存取層（唯一直接碰 ORM 的層）。

佇列列以 SELECT … FOR UPDATE 取得（拋檔/回執狀態變更序列化錨點，沿 D-1 模式），
避免同一列被並發拋檔/標記造成 attempts 或狀態競態。
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.einvoice.models import (
    EInvoiceResultEvent,
    EInvoiceUploadQueue,
    Invoice,
    InvoiceAllowance,
)
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceIssueChannel,
    EInvoiceMessageType,
    InvoiceStatus,
    UploadStatus,
)


class EInvoiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 發票 ──

    async def add_invoice(self, invoice: Invoice) -> Invoice:
        self._session.add(invoice)
        await self._session.flush()
        return invoice

    async def get_invoice(
        self,
        store_id: int,
        invoice_id: int,
        *,
        for_update: bool = False,
    ) -> Invoice | None:
        stmt = select(Invoice).where(Invoice.id == invoice_id, Invoice.store_id == store_id)
        if for_update:
            stmt = stmt.with_for_update()
        result: Invoice | None = await self._session.scalar(stmt)
        return result

    async def issue_channels_for_sales(
        self, store_id: int, sale_ids: list[int]
    ) -> dict[int, tuple[EInvoiceIssueChannel, bool]]:
        """(sale_id → (issue_channel, print_mark))；沒有發票的銷售不列入。

        `print_mark` 一併回：存了載具或捐贈的發票依規定不印證明聯，交易紀錄要據此
        不顯示列印按鈕——後端雖有守衛，但讓店員按了才被擋是白做工（Codex 審查）。
        """
        stmt = select(Invoice.sale_id, Invoice.issue_channel, Invoice.print_mark).where(
            Invoice.store_id == store_id, Invoice.sale_id.in_(sale_ids)
        )
        rows = await self._session.execute(stmt)
        return {sale_id: (channel, mark) for sale_id, channel, mark in rows.all()}

    async def list_pending_invoice_sale_ids(
        self, store_id: int, *, limit: int, offset: int
    ) -> list[int]:
        """仍待開立（PENDING）的發票所屬銷售 id，新到舊；不限日期。"""
        stmt = (
            select(Invoice.sale_id)
            .where(Invoice.store_id == store_id, Invoice.status == InvoiceStatus.PENDING)
            .order_by(Invoice.sale_id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.scalars(stmt)
        return list(result)

    async def find_invoice_by_sale(self, store_id: int, sale_id: int) -> Invoice | None:
        """以 sale_id 找既有發票（一筆銷售至多一張發票，冪等重入用）。"""
        stmt = select(Invoice).where(Invoice.store_id == store_id, Invoice.sale_id == sale_id)
        result: Invoice | None = await self._session.scalar(stmt)
        return result

    async def mark_proof_printed(self, invoice: Invoice, printed_at: datetime) -> None:
        """記下證明聯印出的時間；**已有時間就不覆蓋**——「列印一次」指最初那次。"""
        if invoice.proof_printed_at is None:
            invoice.proof_printed_at = printed_at
            await self._session.flush()

    # ── 折讓 ──

    async def add_allowance(self, allowance: InvoiceAllowance) -> InvoiceAllowance:
        self._session.add(allowance)
        await self._session.flush()
        return allowance

    async def sum_allowances_total(self, store_id: int, invoice_id: int) -> Decimal:
        """某發票已開折讓的累計金額（供超額守衛：Σ 折讓 + 本次 ≤ 發票總額）。"""
        stmt = select(func.coalesce(func.sum(InvoiceAllowance.total), 0)).where(
            InvoiceAllowance.store_id == store_id,
            InvoiceAllowance.invoice_id == invoice_id,
        )
        value = await self._session.scalar(stmt)
        return Decimal(value if value is not None else 0)

    async def sum_allowances_amounts(
        self,
        store_id: int,
        invoice_id: int,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return cumulative net, tax and total for one invoice's allowances."""
        row = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(InvoiceAllowance.net), 0),
                    func.coalesce(func.sum(InvoiceAllowance.tax), 0),
                    func.coalesce(func.sum(InvoiceAllowance.total), 0),
                ).where(
                    InvoiceAllowance.store_id == store_id,
                    InvoiceAllowance.invoice_id == invoice_id,
                )
            )
        ).one()
        return Decimal(row[0]), Decimal(row[1]), Decimal(row[2])

    async def find_allowance_by_return(
        self, store_id: int, return_id: int
    ) -> InvoiceAllowance | None:
        """以退貨單找既有折讓（一退貨至多一折讓；重呼防重複）。"""
        stmt = select(InvoiceAllowance).where(
            InvoiceAllowance.store_id == store_id,
            InvoiceAllowance.return_id == return_id,
        )
        result: InvoiceAllowance | None = await self._session.scalar(stmt)
        return result

    async def list_allowance_queue_items_for_invoice(
        self, store_id: int, invoice_id: int
    ) -> list[EInvoiceUploadQueue]:
        """某發票**所有折讓**的佇列列。

        折讓佇列列以 `allowance_id` 關聯（`invoice_id` 為空），故不能用
        `list_queue_items_for_invoice` 取得——必須經 invoice_allowances 轉一手。
        """
        stmt = (
            select(EInvoiceUploadQueue)
            .join(InvoiceAllowance, InvoiceAllowance.id == EInvoiceUploadQueue.allowance_id)
            .where(
                EInvoiceUploadQueue.store_id == store_id,
                InvoiceAllowance.invoice_id == invoice_id,
            )
        )
        return list((await self._session.scalars(stmt)).all())

    # ── 佇列 ──

    async def add_queue_item(self, item: EInvoiceUploadQueue) -> EInvoiceUploadQueue:
        self._session.add(item)
        await self._session.flush()
        return item

    async def list_queue_items_for_invoice(
        self, store_id: int, invoice_id: int
    ) -> list[EInvoiceUploadQueue]:
        """某發票的所有佇列列（作廢時中止其待送 F0401 用）。"""
        stmt = select(EInvoiceUploadQueue).where(
            EInvoiceUploadQueue.store_id == store_id,
            EInvoiceUploadQueue.invoice_id == invoice_id,
        )
        return list((await self._session.scalars(stmt)).all())

    async def lock_queue_items_for_invoice(
        self, store_id: int, invoice_id: int
    ) -> list[EInvoiceUploadQueue]:
        """某發票的所有佇列列（FOR UPDATE、刷新到已提交狀態）。

        作廢判斷「取消 vs 在途」必須與交付協議（_expose_and_confirm 持列鎖寫檔）同鎖序列化，
        否則可能讀到過期的未認領列、在另一 worker 曝光檔案後才取消（Codex 第五輪）。
        """
        stmt = (
            select(EInvoiceUploadQueue)
            .where(
                EInvoiceUploadQueue.store_id == store_id,
                EInvoiceUploadQueue.invoice_id == invoice_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_other_unresolved_allowance_items(
        self, store_id: int, invoice_id: int, *, exclude_queue_id: int
    ) -> int:
        """同一發票「其他」尚未成功終結的折讓佇列列數（sale 級 ALLOWANCE 轉移守門）。

        未解決＝非 UPLOADED 亦非 CANCELLED——**FAILED 也算未解決**（Codex 第八輪：一張折讓
        失敗、另一張成功時，sale 不得被標成已折讓完成；失敗列須 retry 收斂後才轉）。
        """
        stmt = (
            select(func.count())
            .select_from(EInvoiceUploadQueue)
            .join(
                InvoiceAllowance,
                InvoiceAllowance.id == EInvoiceUploadQueue.allowance_id,
            )
            .where(
                EInvoiceUploadQueue.store_id == store_id,
                EInvoiceUploadQueue.status.notin_([UploadStatus.UPLOADED, UploadStatus.CANCELLED]),
                EInvoiceUploadQueue.id != exclude_queue_id,
                InvoiceAllowance.invoice_id == invoice_id,
            )
        )
        value = await self._session.scalar(stmt)
        return int(value if value is not None else 0)

    async def get_queue_item(self, store_id: int, queue_id: int) -> EInvoiceUploadQueue | None:
        """無鎖讀佇列列（回執路徑先解析關聯 sale 用——全域鎖序 sale→queue 的前置）。"""
        stmt = select(EInvoiceUploadQueue).where(
            EInvoiceUploadQueue.id == queue_id,
            EInvoiceUploadQueue.store_id == store_id,
        )
        result: EInvoiceUploadQueue | None = await self._session.scalar(stmt)
        return result

    async def lock_queue_item(self, store_id: int, queue_id: int) -> EInvoiceUploadQueue | None:
        """取得佇列列並上 row lock（拋檔/標記/重送前重載持久列，不信任呼叫端物件）。"""
        stmt = (
            select(EInvoiceUploadQueue)
            .where(
                EInvoiceUploadQueue.id == queue_id,
                EInvoiceUploadQueue.store_id == store_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result: EInvoiceUploadQueue | None = await self._session.scalar(stmt)
        return result

    async def list_due_auto_send_items(
        self,
        *,
        actions: Sequence[EInvoiceAction],
        message_types: Sequence[EInvoiceMessageType],
        idle_since: datetime,
        limit: int,
    ) -> list[EInvoiceUploadQueue]:
        """跨店取「到期可自動送出」的待送出佇列列（最舊的先送）。

        退避只套在**已經嘗試過**的列（`xml_path` 有值＝已認領）：那種再送要隔
        `idle_since` 才不會連續重擊平台。**從未嘗試過的立刻送**——否則店長按下作廢後
        還要白等一個退避間隔，平台上那張發票在這段時間裡仍然有效。
        **不接受 action 全集**——呼叫端必須指名，避免有人不慎排空開立佇列。
        """
        stmt = (
            select(EInvoiceUploadQueue)
            .where(
                EInvoiceUploadQueue.status == UploadStatus.PENDING,
                EInvoiceUploadQueue.action.in_(list(actions)),
                # **真正決定打哪支平台端點的是 message_type**（見 send_via_amego 的
                # _AMEGO_ENDPOINTS 對照），`action` 只是我們自己的分類。兩欄之間沒有 DB
                # 約束保證配對，若只篩 action，一列 `action=VOID, message_type=F0401`
                # 就會讓背景真的去開一張發票——安全界線必須釘在會生效的那一欄上。
                EInvoiceUploadQueue.message_type.in_(list(message_types)),
                or_(
                    # 從未嘗試過（未認領）→ 立刻送，不必等退避。店長按下作廢後，
                    # 平台作廢應該是「下一輪就送出」，而不是白等一個退避間隔。
                    EInvoiceUploadQueue.xml_path.is_(None),
                    EInvoiceUploadQueue.updated_at <= idle_since,
                ),
            )
            .order_by(EInvoiceUploadQueue.created_at.asc(), EInvoiceUploadQueue.id.asc())
            .limit(limit)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_queue(
        self,
        store_id: int,
        *,
        status: UploadStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EInvoiceUploadQueue]:
        stmt = select(EInvoiceUploadQueue).where(EInvoiceUploadQueue.store_id == store_id)
        if status is not None:
            stmt = stmt.where(EInvoiceUploadQueue.status == status)
        stmt = stmt.order_by(EInvoiceUploadQueue.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    @staticmethod
    def _needs_attention_clause(
        *, auto_send_actions: Sequence[EInvoiceAction], stalled_before: datetime
    ) -> ColumnElement[bool]:
        """「需要人處理」的定義——**只有這一份**。

        紅點與清單必須同口徑：紅點說有 1 筆、點進去卻看到「沒有符合的項目」，
        等於畫面對店長說謊（Codex 第六輪）。
        平台退回的，加上超過門檻仍未送出的作廢/折讓（自動送出正常時約一分鐘就清掉）。
        """
        return or_(
            EInvoiceUploadQueue.status == UploadStatus.FAILED,
            and_(
                EInvoiceUploadQueue.status == UploadStatus.PENDING,
                EInvoiceUploadQueue.action.in_(list(auto_send_actions)),
                EInvoiceUploadQueue.created_at <= stalled_before,
            ),
        )

    async def count_needing_attention(
        self,
        store_id: int,
        *,
        auto_send_actions: Sequence[EInvoiceAction],
        stalled_before: datetime,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(EInvoiceUploadQueue)
            .where(
                EInvoiceUploadQueue.store_id == store_id,
                self._needs_attention_clause(
                    auto_send_actions=auto_send_actions, stalled_before=stalled_before
                ),
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_queue(
        self,
        store_id: int,
        *,
        status: UploadStatus | None = None,
        needs_attention: tuple[Sequence[EInvoiceAction], datetime] | None = None,
    ) -> int:
        """符合篩選的佇列總筆數（供分頁）。"""
        stmt = (
            select(func.count())
            .select_from(EInvoiceUploadQueue)
            .where(EInvoiceUploadQueue.store_id == store_id)
        )
        if status is not None:
            stmt = stmt.where(EInvoiceUploadQueue.status == status)
        if needs_attention is not None:
            actions, stalled_before = needs_attention
            stmt = stmt.where(
                self._needs_attention_clause(
                    auto_send_actions=actions, stalled_before=stalled_before
                )
            )
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_queue_with_context(
        self,
        store_id: int,
        *,
        status: UploadStatus | None = None,
        needs_attention: tuple[Sequence[EInvoiceAction], datetime] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[EInvoiceUploadQueue, str | None, int | None]]:
        """同 `list_queue`，另帶回發票號碼與交易編號（供佇列頁顯示人看得懂的識別）。

        折讓列掛的是 `allowance_id`（`invoice_id` 為空），故經折讓再接回發票——
        兩條路徑都要，否則折讓列在畫面上會是一片空白。
        """
        invoice_via_allowance = (
            select(InvoiceAllowance.id.label("allowance_id"), Invoice.invoice_no, Invoice.sale_id)
            .join(Invoice, Invoice.id == InvoiceAllowance.invoice_id)
            .subquery()
        )
        stmt = (
            select(
                EInvoiceUploadQueue,
                func.coalesce(Invoice.invoice_no, invoice_via_allowance.c.invoice_no),
                func.coalesce(Invoice.sale_id, invoice_via_allowance.c.sale_id),
            )
            .outerjoin(Invoice, Invoice.id == EInvoiceUploadQueue.invoice_id)
            .outerjoin(
                invoice_via_allowance,
                invoice_via_allowance.c.allowance_id == EInvoiceUploadQueue.allowance_id,
            )
            .where(EInvoiceUploadQueue.store_id == store_id)
        )
        if status is not None:
            stmt = stmt.where(EInvoiceUploadQueue.status == status)
        if needs_attention is not None:
            actions, stalled_before = needs_attention
            stmt = stmt.where(
                self._needs_attention_clause(
                    auto_send_actions=actions, stalled_before=stalled_before
                )
            )
        stmt = stmt.order_by(EInvoiceUploadQueue.id.desc()).limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], row[1], row[2]) for row in rows]

    # ── 回執事件 ──

    async def add_result_event(self, event: EInvoiceResultEvent) -> EInvoiceResultEvent:
        self._session.add(event)
        await self._session.flush()
        return event
