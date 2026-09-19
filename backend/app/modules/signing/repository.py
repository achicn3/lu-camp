"""signing 資料存取：簽署任務、狀態事件與切結書版本。"""

from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.signing.models import AgreementVersion, SignatureTask, SignatureTaskEvent
from app.shared.enums import SignatureTaskKind, SignatureTaskStatus


def _visible_to_terminal(pos_terminal_id: int | None) -> ColumnElement[bool]:
    """任務是否屬於「現在這台櫃檯」。

    `pos_terminal_id=None` 代表呼叫端不分櫃檯（例如建立任務時檢查這台平板上有沒有別的
    任務——那道唯一索引本來就只看裝置）。任務自己的 `pos_terminal_id` 為 NULL 是本欄
    上線前的舊列，視為不限櫃檯，沿用舊行為。
    """
    if pos_terminal_id is None:
        return true()
    return or_(
        SignatureTask.pos_terminal_id.is_(None),
        SignatureTask.pos_terminal_id == pos_terminal_id,
    )


class SigningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, task: SignatureTask) -> SignatureTask:
        self._session.add(task)
        await self._session.flush()
        return task

    async def add_event(self, event: SignatureTaskEvent) -> SignatureTaskEvent:
        self._session.add(event)
        await self._session.flush()
        return event

    async def get(self, store_id: int, task_id: int) -> SignatureTask | None:
        result: SignatureTask | None = await self._session.scalar(
            select(SignatureTask).where(
                SignatureTask.store_id == store_id, SignatureTask.id == task_id
            )
        )
        return result

    async def get_for_update(self, store_id: int, task_id: int) -> SignatureTask | None:
        """FOR UPDATE 鎖定任務列（D-1 模式）：序列化 sign/cancel 的並發狀態轉換。"""
        result: SignatureTask | None = await self._session.scalar(
            select(SignatureTask)
            .where(SignatureTask.store_id == store_id, SignatureTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result

    async def active_for_device(
        self,
        store_id: int,
        device_id: int,
        *,
        for_update: bool = False,
        pos_terminal_id: int | None = None,
    ) -> SignatureTask | None:
        """裝置目前可見的唯一任務；SIGNED 仍須顯示等待店員完成結帳。"""
        stmt = (
            select(SignatureTask)
            .where(
                SignatureTask.store_id == store_id,
                SignatureTask.kiosk_device_id == device_id,
                _visible_to_terminal(pos_terminal_id),
                SignatureTask.status.in_(
                    (
                        SignatureTaskStatus.PENDING,
                        SignatureTaskStatus.SIGNING,
                        SignatureTaskStatus.SIGNED,
                    )
                ),
            )
            .order_by(SignatureTask.id.desc())
            .limit(1)
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result: SignatureTask | None = await self._session.scalar(stmt)
        return result

    async def get_for_device(
        self,
        store_id: int,
        device_id: int,
        task_id: int,
        *,
        for_update: bool = False,
        pos_terminal_id: int | None = None,
    ) -> SignatureTask | None:
        stmt = select(SignatureTask).where(
            SignatureTask.id == task_id,
            SignatureTask.store_id == store_id,
            SignatureTask.kiosk_device_id == device_id,
            _visible_to_terminal(pos_terminal_id),
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result: SignatureTask | None = await self._session.scalar(stmt)
        return result

    async def expirable_tasks(self, now: datetime) -> list[SignatureTask]:
        """只列出候選；逐筆轉移時由 service 依 cart→task 的全域順序加鎖。"""
        rows = await self._session.scalars(
            select(SignatureTask)
            .where(
                SignatureTask.status.in_(
                    (
                        SignatureTaskStatus.PENDING,
                        SignatureTaskStatus.SIGNING,
                        SignatureTaskStatus.SIGNED,
                    )
                ),
                SignatureTask.expires_at.is_not(None),
                SignatureTask.expires_at <= now,
            )
            .order_by(SignatureTask.id)
        )
        return list(rows)

    async def due_signature_images(self, now: datetime) -> list[SignatureTask]:
        rows = await self._session.scalars(
            select(SignatureTask)
            .where(
                SignatureTask.signature_image.is_not(None),
                SignatureTask.signature_retention_until.is_not(None),
                SignatureTask.signature_retention_until <= now,
                SignatureTask.signature_cleanup_reported_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        return list(rows)

    async def signature_retention_report(
        self,
        store_id: int,
        *,
        limit: int,
    ) -> list[SignatureTask]:
        rows = await self._session.scalars(
            select(SignatureTask)
            .where(
                SignatureTask.store_id == store_id,
                SignatureTask.signature_cleanup_reported_at.is_not(None),
            )
            .order_by(SignatureTask.signature_cleanup_reported_at.desc())
            .limit(limit)
        )
        return list(rows)

    @staticmethod
    def _tasks_where(
        stmt: Any,
        store_id: int,
        status: SignatureTaskStatus | None,
        kind: SignatureTaskKind | None,
        contact_id: int | None,
    ) -> Any:
        """簽署任務的篩選條件只寫這一份——清單與總筆數共用，兩者才不會各走各的。"""
        stmt = stmt.where(SignatureTask.store_id == store_id)
        if status is not None:
            stmt = stmt.where(SignatureTask.status == status)
        if kind is not None:
            stmt = stmt.where(SignatureTask.kind == kind)
        if contact_id is not None:
            stmt = stmt.where(SignatureTask.contact_id == contact_id)
        return stmt

    async def count_tasks(
        self,
        store_id: int,
        status: SignatureTaskStatus | None,
        *,
        kind: SignatureTaskKind | None = None,
        contact_id: int | None = None,
    ) -> int:
        """符合同一組篩選的簽署任務總筆數（不分頁；清單頁算總頁數用）。"""
        stmt = self._tasks_where(
            select(func.count()).select_from(SignatureTask), store_id, status, kind, contact_id
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def list_tasks(
        self,
        store_id: int,
        status: SignatureTaskStatus | None,
        *,
        kind: SignatureTaskKind | None = None,
        contact_id: int | None = None,
        limit: int,
        offset: int,
    ) -> list[SignatureTask]:
        stmt = self._tasks_where(select(SignatureTask), store_id, status, kind, contact_id)
        stmt = stmt.order_by(SignatureTask.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def get_agreement_by_version(
        self, store_id: int, version: int
    ) -> AgreementVersion | None:
        result: AgreementVersion | None = await self._session.scalar(
            select(AgreementVersion).where(
                AgreementVersion.store_id == store_id, AgreementVersion.version == version
            )
        )
        return result

    async def get_latest_agreement(self, store_id: int) -> AgreementVersion | None:
        """該店版本號最大的一列＝目前生效的切結書。"""
        result: AgreementVersion | None = await self._session.scalar(
            select(AgreementVersion)
            .where(AgreementVersion.store_id == store_id)
            .order_by(AgreementVersion.version.desc())
            .limit(1)
        )
        return result

    async def get_agreement_by_id(self, agreement_id: int) -> AgreementVersion | None:
        return await self._session.get(AgreementVersion, agreement_id)

    async def add_agreement(self, agreement: AgreementVersion) -> AgreementVersion:
        self._session.add(agreement)
        await self._session.flush()
        return agreement
