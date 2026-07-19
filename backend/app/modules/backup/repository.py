"""backup/restore 狀態表的資料存取（唯一直接碰 backup_runs/restore_runs 的層）。"""

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.backup.models import BackupRun, RestoreRun
from app.shared.enums import BackupStatus


class BackupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_running(self, store_id: int) -> BackupRun | None:
        """該店進行中（RUNNING）的備份（單一在跑守衛用）。"""
        result: BackupRun | None = await self._session.scalar(
            select(BackupRun).where(
                BackupRun.store_id == store_id,
                BackupRun.status == BackupStatus.RUNNING,
            )
        )
        return result

    async def add_run(self, run: BackupRun) -> BackupRun:
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_run(self, store_id: int, run_id: int) -> BackupRun | None:
        """單筆備份（限本店;手動觸發後輪詢狀態用）。"""
        result: BackupRun | None = await self._session.scalar(
            select(BackupRun).where(BackupRun.id == run_id, BackupRun.store_id == store_id)
        )
        return result

    async def get_succeeded_by_r2_key(self, store_id: int, r2_key: str) -> BackupRun | None:
        """依 r2_key 找該店一筆 SUCCEEDED 備份（還原來源綁定用:只能還原目錄內的已知good備份）。"""
        result: BackupRun | None = await self._session.scalar(
            select(BackupRun).where(
                BackupRun.store_id == store_id,
                BackupRun.r2_key == r2_key,
                BackupRun.status == BackupStatus.SUCCEEDED,
            )
        )
        return result

    async def list_runs(self, store_id: int, *, limit: int = 30) -> list[BackupRun]:
        stmt = (
            select(BackupRun)
            .where(BackupRun.store_id == store_id)
            .order_by(desc(BackupRun.id))
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return list(result)

    async def last_success_at(self, store_id: int) -> datetime | None:
        """最近一次成功備份的完成時間（健康度/到期判斷用）。"""
        return await self._session.scalar(
            select(BackupRun.finished_at)
            .where(
                BackupRun.store_id == store_id,
                BackupRun.status == BackupStatus.SUCCEEDED,
            )
            .order_by(desc(BackupRun.finished_at))
            .limit(1)
        )

    async def add_restore(self, run: RestoreRun) -> RestoreRun:
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_restore(self, store_id: int, restore_id: int) -> RestoreRun | None:
        result: RestoreRun | None = await self._session.scalar(
            select(RestoreRun).where(
                RestoreRun.id == restore_id, RestoreRun.store_id == store_id
            )
        )
        return result

    async def list_restores(self, store_id: int, *, limit: int = 30) -> list[RestoreRun]:
        stmt = (
            select(RestoreRun)
            .where(RestoreRun.store_id == store_id)
            .order_by(desc(RestoreRun.id))
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return list(result)
