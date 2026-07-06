"""signing 資料存取：簽署任務與切結書版本（唯一可碰 signing 資料表的層）。"""

from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.signing.models import AgreementVersion, SignatureTask
from app.shared.enums import SignatureTaskStatus


class SigningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, task: SignatureTask) -> SignatureTask:
        self._session.add(task)
        await self._session.flush()
        return task

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
        )
        return result

    async def cancel_pending_tasks(self, store_id: int) -> int:
        """作廢同店所有 PENDING 任務（重推＝舊單作廢；守護「同時最多一件待簽」）。"""
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(SignatureTask)
                .where(
                    SignatureTask.store_id == store_id,
                    SignatureTask.status == SignatureTaskStatus.PENDING,
                )
                .values(status=SignatureTaskStatus.CANCELLED, cancelled_at=func.now())
            ),
        )
        return result.rowcount or 0

    async def latest_pending(self, store_id: int) -> SignatureTask | None:
        """最新一筆 PENDING（手持端輪詢用；單店單裝置，同時最多一件在簽）。"""
        result: SignatureTask | None = await self._session.scalar(
            select(SignatureTask)
            .where(
                SignatureTask.store_id == store_id,
                SignatureTask.status == SignatureTaskStatus.PENDING,
            )
            .order_by(SignatureTask.id.desc())
            .limit(1)
        )
        return result

    async def list_tasks(
        self,
        store_id: int,
        status: SignatureTaskStatus | None,
        *,
        limit: int,
        offset: int,
    ) -> list[SignatureTask]:
        stmt = select(SignatureTask).where(SignatureTask.store_id == store_id)
        if status is not None:
            stmt = stmt.where(SignatureTask.status == status)
        stmt = stmt.order_by(SignatureTask.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def get_agreement_by_version(self, version: int) -> AgreementVersion | None:
        result: AgreementVersion | None = await self._session.scalar(
            select(AgreementVersion).where(AgreementVersion.version == version)
        )
        return result

    async def get_agreement_by_id(self, agreement_id: int) -> AgreementVersion | None:
        return await self._session.get(AgreementVersion, agreement_id)

    async def add_agreement(self, agreement: AgreementVersion) -> AgreementVersion:
        self._session.add(agreement)
        await self._session.flush()
        return agreement
